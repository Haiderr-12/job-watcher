"""
Amazon UK job watcher.

Checks https://www.jobsatamazon.co.uk for new jobs and sends a Discord
message for every new job that matches the filters below.

How it reads jobs: the Amazon site is a JavaScript app whose job list comes
from a GraphQL API at https://www.jobsatamazon.co.uk/graphql (anonymous
access, no login needed). We call that API directly with plain HTTP.
If that ever stops working, we automatically fall back to loading the real
page in a headless (invisible) Chrome via Playwright and reading the same
API response off the network.

Exit codes: 0 always (so GitHub Actions still commits state.json),
except 1 when the DISCORD_WEBHOOK environment variable is missing.
"""

import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timezone

import requests

# ============================================================================
# CONFIG — edit this block to change what you get alerted about
# ============================================================================

# A job alerts if ANY of these words appears in its title, city, postcode or
# site name (case doesn't matter). Empty list [] = alert for ALL UK jobs.
LOCATION_TERMS = [
    "ilford", "romford", "barking", "dagenham", "rainham", "london",
    "tilbury", "grays", "purfleet", "basildon", "thurrock", "essex",
    "dartford", "erith", "belvedere", "enfield", "croydon", "weybridge",
]

# Only alert for jobs whose TITLE contains one of these words.
# Empty list [] = all roles.
TITLE_KEYWORDS = []

# Start each alert with @everyone so Discord pushes a phone notification.
PING_EVERYONE = True

# At most this many individual job alerts per check; any extra new jobs are
# rolled into one "...plus K more" message.
MAX_ALERTS_PER_RUN = 15

# ============================================================================
# End of config
# ============================================================================

GRAPHQL_URL = "https://www.jobsatamazon.co.uk/graphql"
SEARCH_PAGE = "https://www.jobsatamazon.co.uk/app#/jobSearch"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MAX_SEEN = 2000          # cap on remembered job ids
FAILS_BEFORE_WARNING = 4  # consecutive failed checks before we warn on Discord

# The GraphQL query the Amazon site itself uses (captured from the live site).
JOB_QUERY = """query searchJobCardsByLocation($searchJobRequest: SearchJobRequest!) {
  searchJobCardsByLocation(searchJobRequest: $searchJobRequest) {
    nextToken
    jobCards {
      jobId
      jobTitle
      jobType
      employmentType
      city
      state
      postalCode
      locationName
      totalPayRateMin
      totalPayRateMax
      currencyCode
      jobTypeL10N
      employmentTypeL10N
      totalPayRateMinL10N
      totalPayRateMaxL10N
      scheduleCount
      __typename
    }
    __typename
  }
}
"""


# ----------------------------------------------------------------------------
# Reading jobs — primary path: call the API directly (fast, no browser)
# ----------------------------------------------------------------------------

# Amazon's firewall blocks datacenter IPs (including GitHub's servers), so a
# plain call from the cloud is refused with "403 Forbidden". To get around
# that we can forward the request through a free public relay whose own IP
# Amazon allows. We try the direct call first (it works from a home/UK
# connection, e.g. when you test on your PC), then each relay in turn.
# A relay entry is a URL prefix that gets stuck in front of the real address.
RELAYS = [
    "",                          # direct — no relay (works from a home IP)
    "https://proxy.cors.sh/",    # free relay that forwards from an allowed IP
]


def _search_page(relay, next_token):
    """Do one API call (optionally via a relay) and return the result block."""
    request_vars = {
        "locale": "en-GB",
        "country": "United Kingdom",
        "keyWords": "",
        "equalFilters": [],
        "containFilters": [{"key": "isPrivateSchedule", "val": ["true", "false"]}],
        "rangeFilters": [],
        "orFilters": [],
        "dateFilters": [],
        "sorters": [{"fieldName": "totalPayRateMax", "ascending": "false"}],
        "pageSize": 100,
        "consolidateSchedule": True,
    }
    if next_token:
        request_vars["nextToken"] = next_token
    headers = {
        # This is the site's own anonymous-visitor token, not a secret.
        "authorization": "Bearer Status|unauthenticated|Session|",
        "content-type": "application/json",
        "country": "United Kingdom",
        "iscanary": "false",
        "accept": "*/*",
        "accept-language": "en-GB",
        "origin": "https://www.jobsatamazon.co.uk",
        "referer": "https://www.jobsatamazon.co.uk/",
        "user-agent": USER_AGENT,
        "x-amzn-requestld": str(uuid.uuid4()),
        "x-hvh-time": str(int(time.time() * 1000)),
    }
    body = {
        "operationName": "searchJobCardsByLocation",
        "variables": {"searchJobRequest": request_vars},
        "query": JOB_QUERY,
    }
    resp = requests.post(relay + GRAPHQL_URL, json=body, headers=headers, timeout=40)
    resp.raise_for_status()
    payload = resp.json()
    # A relay that itself errors may return HTML or an error object, not our
    # data — treat anything without the expected shape as a failure.
    if "errors" in payload and not payload.get("data"):
        raise RuntimeError(f"API returned errors: {str(payload['errors'])[:120]}")
    return payload["data"]["searchJobCardsByLocation"]


def _fetch_all_pages(relay):
    """Follow pagination for one transport. Returns a list of raw cards."""
    cards = []
    next_token = None
    for _page in range(20):  # safety cap on pages
        result = _search_page(relay, next_token)
        cards.extend(result["jobCards"])
        next_token = result.get("nextToken")
        if not next_token:
            break
    return cards


def fetch_jobs_api():
    """Try the direct API, then each relay. Returns (cards, label).
    Raises RuntimeError only if every transport fails."""
    errors = []
    for relay in RELAYS:
        try:
            cards = _fetch_all_pages(relay)
            return cards, ("direct API" if not relay else f"relay {relay}")
        except Exception as e:
            label = relay or "direct"
            errors.append(f"{label}: {type(e).__name__}: {e}")
            print(f"  transport failed [{label}]: {type(e).__name__}: {e}")
    raise RuntimeError("all API transports failed:\n  " + "\n  ".join(errors))


# ----------------------------------------------------------------------------
# Reading jobs — fallback path: load the real page in an invisible browser
# ----------------------------------------------------------------------------

def fetch_jobs_playwright():
    """Load the job-search page headlessly and read the API response off the
    network. Returns a list of raw job cards. Raises on failure."""
    from playwright.sync_api import sync_playwright

    def run_browser(p):
        browser = p.chromium.launch(headless=True)
        try:
            # A normal user agent matters: with the default "HeadlessChrome"
            # agent the site does not load the job list at all.
            page_ctx = browser.new_context(user_agent=USER_AGENT, locale="en-GB")
            page = page_ctx.new_page()
            responses = []

            def on_response(resp):
                if "/graphql" in resp.url and resp.request.method == "POST":
                    try:
                        responses.append(resp.json())
                    except Exception:
                        pass

            page.on("response", on_response)
            page.goto(SEARCH_PAGE, timeout=60000, wait_until="domcontentloaded")
            for _ in range(60):  # wait up to 30s for the job-search response
                if any("searchJobCardsByLocation" in json.dumps(r) for r in responses):
                    break
                page.wait_for_timeout(500)
            cards = []
            found = False
            for r in responses:
                block = (r.get("data") or {}).get("searchJobCardsByLocation")
                if block is not None and isinstance(block.get("jobCards"), list):
                    found = True
                    cards.extend(block["jobCards"])
            if not found:
                raise RuntimeError("page loaded but job-search API response never appeared")
            return cards
        finally:
            browser.close()

    try:
        with sync_playwright() as p:
            return run_browser(p)
    except Exception as e:
        # If the browser binary is missing (fresh GitHub runner), install it
        # once and retry.
        if "Executable doesn't exist" in str(e) or "playwright install" in str(e):
            import subprocess
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                check=False, timeout=300,
            )
            with sync_playwright() as p:
                return run_browser(p)
        raise


# ----------------------------------------------------------------------------
# Filtering and formatting
# ----------------------------------------------------------------------------

def normalise(card):
    """Pull the fields we care about out of a raw API job card."""
    job_id = card.get("jobId") or ""
    pay_min = card.get("totalPayRateMinL10N") or card.get("totalPayRateMin")
    pay_max = card.get("totalPayRateMaxL10N") or card.get("totalPayRateMax")
    if pay_min and pay_max:
        pay = str(pay_min) if str(pay_min) == str(pay_max) else f"{pay_min} - {pay_max}"
    else:
        pay = "Not listed"
    schedule_bits = [b for b in [card.get("jobTypeL10N") or card.get("jobType"),
                                 card.get("employmentTypeL10N") or card.get("employmentType")] if b]
    location_bits = [b for b in [card.get("locationName"), card.get("city"),
                                 card.get("postalCode")] if b]
    return {
        "jobId": job_id,
        "title": card.get("jobTitle") or "Amazon job",
        "city": card.get("city") or "",
        "postalCode": card.get("postalCode") or "",
        "locationName": card.get("locationName") or "",
        "location": ", ".join(location_bits) or (card.get("state") or "UK"),
        "pay": pay,
        "schedule": " / ".join(schedule_bits) or "Not listed",
        "link": f"https://www.jobsatamazon.co.uk/app#/jobDetail?jobId={job_id}&locale=en-GB",
    }


def matches_filters(job):
    if LOCATION_TERMS:
        haystack = " ".join([job["title"], job["city"], job["postalCode"],
                             job["locationName"]]).lower()
        if not any(term.lower() in haystack for term in LOCATION_TERMS):
            return False
    if TITLE_KEYWORDS:
        title = job["title"].lower()
        if not any(kw.lower() in title for kw in TITLE_KEYWORDS):
            return False
    return True


# ----------------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------------

def discord_send(webhook, content=None, embed=None):
    """Send one Discord message. Returns True on success, False otherwise."""
    payload = {"allowed_mentions": {"parse": ["everyone"]}}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]
    try:
        resp = requests.post(webhook, json=payload, timeout=30)
        if resp.status_code == 429:  # rate limited: wait and retry once
            wait = float(resp.headers.get("Retry-After", "2"))
            time.sleep(min(wait, 30))
            resp = requests.post(webhook, json=payload, timeout=30)
        return resp.status_code < 400
    except requests.RequestException as e:
        print(f"Discord send failed: {e}")
        return False


def job_embed(job):
    return {
        "title": job["title"],
        "url": job["link"],
        "color": 0xFF9900,  # Amazon orange
        "fields": [
            {"name": "Location", "value": job["location"], "inline": True},
            {"name": "Pay", "value": job["pay"], "inline": True},
            {"name": "Schedule", "value": job["schedule"], "inline": True},
        ],
        "footer": {"text": "Tap the title to apply"},
    }


# ----------------------------------------------------------------------------
# State (the bot's memory, committed back to the repo by GitHub Actions)
# ----------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("seen", {})
        state.setdefault("fails", 0)
        state.setdefault("fail_alerted", False)
        state.setdefault("announced", False)
        state.setdefault("heartbeat", "")
        return state
    except (OSError, ValueError):
        return {"seen": {}, "fails": 0, "fail_alerted": False,
                "announced": False, "heartbeat": ""}


def save_state(state):
    # Keep only the newest MAX_SEEN job ids so the file can't grow forever.
    if len(state["seen"]) > MAX_SEEN:
        newest = sorted(state["seen"].items(), key=lambda kv: kv[1], reverse=True)
        state["seen"] = dict(newest[:MAX_SEEN])
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    # Strip whitespace and any stray BOM / zero-width chars that can sneak in
    # when a secret is pasted or set from a shell (﻿ = BOM).
    webhook = os.environ.get("DISCORD_WEBHOOK", "").strip().strip("﻿​").strip()
    if not webhook:
        print("ERROR: DISCORD_WEBHOOK environment variable is not set.")
        return 1

    state = load_state()
    state["heartbeat"] = date.today().isoformat()  # daily commit keeps Actions alive
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ping = "@everyone " if PING_EVERYONE else ""

    # --- Read the job list (direct/relay API first, then browser fallback) -
    cards, how = None, None
    try:
        cards, how = fetch_jobs_api()
    except Exception as e:
        print(f"API read failed: {type(e).__name__}: {e}")
        try:
            cards = fetch_jobs_playwright()
            how = "Playwright fallback"
        except Exception as e2:
            print(f"Playwright fallback failed: {type(e2).__name__}: {e2}")

    # --- Case 1: the check failed (this is NOT the same as "zero jobs") ----
    if cards is None:
        state["fails"] += 1
        print(f"Check FAILED ({state['fails']} consecutive).")
        if state["fails"] >= FAILS_BEFORE_WARNING and not state["fail_alerted"]:
            if discord_send(webhook, content=(
                    f"{ping}⚠️ Amazon job watch: the last "
                    f"{state['fails']} checks could not read the jobs site. "
                    "It may be down or blocking. I'll keep trying and will "
                    "message again when it recovers.")):
                state["fail_alerted"] = True
        save_state(state)
        return 0

    # --- Case 2: the check worked (possibly with zero jobs — that's normal)
    print(f"Read OK via {how}: {len(cards)} job(s) listed UK-wide.")
    if state["fail_alerted"]:
        discord_send(webhook, content=(
            "✅ Amazon job watch: reading the jobs site again — "
            "back to normal."))
    state["fails"] = 0
    state["fail_alerted"] = False

    jobs = [normalise(c) for c in cards]
    matching = [j for j in jobs if j["jobId"] and matches_filters(j)]
    new_jobs = [j for j in matching if j["jobId"] not in state["seen"]]
    print(f"{len(matching)} match filters; {len(new_jobs)} new.")

    # First ever successful read: announce that the watch is live.
    if not state["announced"]:
        if discord_send(webhook, content=(
                f"{ping}✅ Amazon job watch is live — {len(matching)} "
                "matching job(s) right now. You'll get a ping here whenever "
                "a new one appears.")):
            state["announced"] = True
        time.sleep(1)

    # Alert each new job (up to the cap), then one summary for the rest.
    for job in new_jobs[:MAX_ALERTS_PER_RUN]:
        if discord_send(webhook, content=f"{ping}\U0001f4e2 New Amazon job!",
                        embed=job_embed(job)):
            state["seen"][job["jobId"]] = today
            print(f"Alerted: {job['title']} ({job['location']})")
        time.sleep(1)  # be gentle with Discord's rate limits
    extra = new_jobs[MAX_ALERTS_PER_RUN:]
    if extra:
        if discord_send(webhook, content=(
                f"{ping}➕ ...plus {len(extra)} more new job(s) — "
                f"see them all at {SEARCH_PAGE}")):
            for job in extra:
                state["seen"][job["jobId"]] = today

    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # Never crash: exiting 0 lets GitHub Actions commit state.json.
        print(f"Unexpected error: {type(e).__name__}: {e}")
        sys.exit(0)
