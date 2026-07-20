"""
Amazon SHIFT watcher.

Once you've applied to an Amazon job, shifts ("schedules") get released over
the following weeks. Those shifts appear on Amazon's PUBLIC schedule feed
(no login needed), so this bot watches them and pings Discord the moment one
appears near you — you then log in yourself and book it.

It watches two things:
  1. Any exact job you list in WATCH_JOB_IDS (e.g. the Tilbury job you applied
     to — paste its jobId below).
  2. Every Amazon job near a chosen point (WATCH_NEAR), auto-discovered each
     run, so new sites near you are covered too.

It reuses the Amazon bot's connection code (relay fallback) from check_jobs.py.
Exit 0 always (so state still commits) except 1 if the webhook is missing.
"""

import json
import os
import sys
import time
import uuid
from datetime import date, datetime, timezone

import requests
import check_jobs as cj  # reuse RELAYS, transport, USER_AGENT, GRAPHQL_URL

# ============================================================================
# CONFIG
# ============================================================================

# Exact job(s) you've applied to. Paste the jobId from the job's web address
# (the bit after "jobId="), e.g. "JOB-UK-0000001234". You can list several.
WATCH_JOB_IDS = []

# Also auto-watch every Amazon job near this point. Coordinates below are
# Tilbury (RM18); 25 miles covers Tilbury, Dartford, Rainham, Dagenham,
# Belvedere, etc. Set to None to only watch WATCH_JOB_IDS.
WATCH_NEAR = {"lat": 51.4626, "lng": 0.3574, "distance_miles": 25, "label": "Tilbury area"}

PING_EVERYONE = True
MAX_ALERTS_PER_RUN = 10

# ============================================================================

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shifts_state.json")
FAILS_BEFORE_WARNING = 4

SCHEDULE_QUERY = """query searchScheduleCards($searchScheduleRequest: SearchScheduleRequest!) {
  searchScheduleCards(searchScheduleRequest: $searchScheduleRequest) {
    nextToken
    scheduleCards {
      scheduleId
      jobId
      externalJobTitle
      city
      state
      postalCode
      address
      hireStartDate
      firstDayOnSite
      hoursPerWeek
      scheduleText
      scheduleType
      employmentType
      basePay
      totalPayRate
      totalPayRateL10N
      currencyCode
      laborDemandAvailableCount
      __typename
    }
    __typename
  }
}
"""


# ----------------------------------------------------------------------------
# Reading data (reusing the Amazon bot's relay fallback)
# ----------------------------------------------------------------------------

def _headers():
    return {
        "authorization": "Bearer Status|unauthenticated|Session|",
        "content-type": "application/json",
        "country": "United Kingdom",
        "iscanary": "false",
        "accept": "*/*",
        "accept-language": "en-GB",
        "origin": "https://www.jobsatamazon.co.uk",
        "referer": "https://www.jobsatamazon.co.uk/",
        "user-agent": cj.USER_AGENT,
        "x-amzn-requestld": str(uuid.uuid4()),
        "x-hvh-time": str(int(time.time() * 1000)),
    }


def fetch_schedules(job_id):
    """Return the list of schedule cards for one job, trying each transport."""
    body = {
        "operationName": "searchScheduleCards",
        "variables": {"searchScheduleRequest": {
            "locale": "en-GB", "country": "United Kingdom", "keyWords": "",
            "equalFilters": [],
            "containFilters": [{"key": "isPrivateSchedule", "val": ["true", "false"]}],
            "rangeFilters": [], "orFilters": [], "dateFilters": [],
            "sorters": [{"fieldName": "totalPayRateMax", "ascending": "false"}],
            "pageSize": 1000, "jobId": job_id, "consolidateSchedule": True,
        }},
        "query": SCHEDULE_QUERY,
    }
    last = None
    for relay in cj.RELAYS:
        try:
            resp = requests.post(relay + cj.GRAPHQL_URL, json=body,
                                 headers=_headers(), timeout=40)
            resp.raise_for_status()
            data = resp.json()
            block = data["data"]["searchScheduleCards"]
            return block.get("scheduleCards") or []
        except Exception as e:
            last = e
            continue
    raise last if last else RuntimeError("no transport")


def discover_job_ids():
    """Amazon jobIds near WATCH_NEAR (returns [] if disabled)."""
    if not WATCH_NEAR:
        return []
    geo = {"lat": WATCH_NEAR["lat"], "lng": WATCH_NEAR["lng"],
           "distance_miles": WATCH_NEAR["distance_miles"]}
    last = None
    for relay in cj.RELAYS:
        try:
            block = cj._search_page(relay, None, geo=geo)
            return [c["jobId"] for c in block.get("jobCards", []) if c.get("jobId")]
        except Exception as e:
            last = e
            continue
    raise last if last else RuntimeError("no transport")


# ----------------------------------------------------------------------------
# Discord + state (same patterns as the Amazon bot)
# ----------------------------------------------------------------------------

def discord_send(webhook, content=None, embed=None):
    payload = {"allowed_mentions": {"parse": ["everyone"]}}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]
    try:
        r = requests.post(webhook, json=payload, timeout=30)
        if r.status_code == 429:
            time.sleep(min(float(r.headers.get("Retry-After", "2")), 30))
            r = requests.post(webhook, json=payload, timeout=30)
        return r.status_code < 400
    except requests.RequestException as e:
        print(f"Discord send failed: {e}")
        return False


def shift_embed(s):
    pay = s.get("totalPayRateL10N") or (f"£{s.get('totalPayRate')}" if s.get("totalPayRate") else "See listing")
    where = ", ".join([x for x in [s.get("address"), s.get("city"), s.get("postalCode")] if x]) or "Amazon site"
    slots = s.get("laborDemandAvailableCount")
    fields = [
        {"name": "Site", "value": where, "inline": False},
        {"name": "Start date", "value": str(s.get("hireStartDate") or "?"), "inline": True},
        {"name": "Hours/week", "value": str(s.get("hoursPerWeek") or "?"), "inline": True},
        {"name": "Pay", "value": f"{pay}/hr", "inline": True},
        {"name": "Shift", "value": str(s.get("scheduleText") or s.get("scheduleType") or "?"), "inline": False},
    ]
    if slots is not None:
        fields.append({"name": "Slots open", "value": str(slots), "inline": True})
    link = f"https://www.jobsatamazon.co.uk/app#/jobDetail?jobId={s.get('jobId')}&locale=en-GB"
    return {
        "title": f"🚨 Shift available — {s.get('externalJobTitle') or 'Amazon job'}",
        "url": link,
        "color": 0xE74C3C,  # red = act now
        "fields": fields,
        "footer": {"text": "Log in to your Amazon account and book it fast"},
    }


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault("seen_shifts", {})
    s.setdefault("fails", 0)
    s.setdefault("fail_alerted", False)
    s.setdefault("announced", False)
    s.setdefault("heartbeat", "")
    return s


def save_state(s):
    if len(s["seen_shifts"]) > 2000:
        newest = sorted(s["seen_shifts"].items(), key=lambda kv: kv[1], reverse=True)
        s["seen_shifts"] = dict(newest[:2000])
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    webhook = os.environ.get("DISCORD_WEBHOOK", "").strip().strip("﻿​").strip()
    if not webhook:
        print("ERROR: DISCORD_WEBHOOK is not set.")
        return 1

    state = load_state()
    state["heartbeat"] = date.today().isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ping = "@everyone " if PING_EVERYONE else ""

    # Build the list of jobs to check: your saved ones + any near WATCH_NEAR.
    job_ids = list(dict.fromkeys(WATCH_JOB_IDS))  # keep order, dedupe
    reachable = False
    try:
        for jid in discover_job_ids():
            if jid not in job_ids:
                job_ids.append(jid)
        reachable = True
    except Exception as e:
        print(f"Job discovery failed: {type(e).__name__}: {e}")

    # Gather all current schedules across those jobs.
    schedules = []
    for jid in job_ids:
        try:
            schedules.extend(fetch_schedules(jid))
            reachable = True
        except Exception as e:
            print(f"Schedule read failed for {jid}: {type(e).__name__}: {e}")

    # Case 1: couldn't reach the API at all -> a real failure.
    if not reachable:
        state["fails"] += 1
        print(f"Check FAILED ({state['fails']} consecutive).")
        if state["fails"] >= FAILS_BEFORE_WARNING and not state["fail_alerted"]:
            if discord_send(webhook, content=(
                    f"{ping}⚠️ Shift watch: couldn't read Amazon for "
                    f"{state['fails']} checks. Still trying.")):
                state["fail_alerted"] = True
        save_state(state)
        return 0

    # Case 2: reachable (0 schedules is normal — it means none are open yet).
    if state["fail_alerted"]:
        discord_send(webhook, content="✅ Shift watch: reading Amazon again.")
    state["fails"] = 0
    state["fail_alerted"] = False

    label = WATCH_NEAR["label"] if WATCH_NEAR else "your saved jobs"

    # Only count a shift as catchable if it actually has slots open. A shift
    # that shows 0 available isn't bookable yet; if it later opens up it will
    # (correctly) look "new" again and alert then.
    def bookable(s):
        cnt = s.get("laborDemandAvailableCount")
        return cnt is None or cnt > 0

    open_now = [s for s in schedules if bookable(s)]
    print(f"Checked {len(job_ids)} job(s); {len(open_now)} bookable shift(s) now.")

    new = [s for s in open_now if s.get("scheduleId")
           and s["scheduleId"] not in state["seen_shifts"]]

    if not state["announced"]:
        discord_send(webhook, content=(
            f"{ping}✅ Amazon shift watch is live — watching {label}"
            f"{' + your job(s)' if WATCH_JOB_IDS else ''}. "
            f"{len(open_now)} shift(s) open right now; I'll ping you the "
            "moment a new one appears, even overnight."))
        state["announced"] = True
        time.sleep(1)

    for s in new[:MAX_ALERTS_PER_RUN]:
        if discord_send(webhook, content=f"{ping}🚨 Amazon shift just opened!",
                        embed=shift_embed(s)):
            state["seen_shifts"][s["scheduleId"]] = today
            print(f"Alerted shift {s['scheduleId']} at {s.get('city')}")
        time.sleep(1)
    extra = new[MAX_ALERTS_PER_RUN:]
    if extra:
        if discord_send(webhook, content=(
                f"{ping}➕ ...plus {len(extra)} more shift(s) just opened — "
                "check your Amazon dashboard now.")):
            for s in extra:
                state["seen_shifts"][s["scheduleId"]] = today

    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Unexpected error: {type(e).__name__}: {e}")
        sys.exit(0)
