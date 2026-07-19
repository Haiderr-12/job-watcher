"""
All-jobs watcher (near London) via the Adzuna jobs API.

This is the WIDE NET that complements the Amazon bot: it watches entry-level,
no-CV / no-experience jobs across every employer and agency near you
(warehouse, retail, hospitality, etc.), and pings a Discord channel when a
new one appears. Driving roles are filtered out.

Data comes from Adzuna (https://developer.adzuna.com) - a free jobs API that
aggregates most UK job boards. Because Adzuna re-indexes other sites, its data
lags real-time by a few hours, so there's no point hammering it - we check
every ~15 minutes, which also keeps us inside the free usage limit.

Exit codes: 0 always (so GitHub Actions still commits state), except 1 if a
required secret (webhook or Adzuna keys) is missing.
"""

import json
import os
import sys
import time
from datetime import date, datetime, timezone

import requests

# ============================================================================
# CONFIG — edit this block to change what you get alerted about
# ============================================================================

# Where to search from, and how far out (miles). ~10 miles ≈ 1 hour by local
# transport from Ilford (IG3).
WHERE = "Ilford"
DISTANCE_MILES = 10

# Only alert for jobs paying at least this much per hour. Set to 0 to turn the
# pay filter off. NOTE: many listings don't state a wage, so this uses
# Adzuna's salary figure/estimate and assumes the hours below — it's a helpful
# bias toward better-paid roles, not an exact guarantee.
MIN_HOURLY_PAY = 15
ASSUMED_HOURS_PER_WEEK = 37.5   # used to convert the hourly floor to a yearly one

# Which Adzuna job categories to watch. Full list of tags is printed by the
# Adzuna "categories" endpoint; these are the entry-level, no-experience ones.
# Add "manufacturing-jobs", "part-time-jobs", "other-general-jobs" to widen.
CATEGORIES = [
    "logistics-warehouse-jobs",
    "retail-jobs",
    "hospitality-catering-jobs",
    "domestic-help-cleaning-jobs",
]

# Skip any job whose title contains one of these words. Two groups:
#  - driving roles (you don't drive)
#  - senior roles (need experience) so only entry-level jobs come through.
EXCLUDE_TITLE_WORDS = [
    # driving
    "driver", "driving", "hgv", "lgv", "7.5", "class 1", "class 2",
    "cat c", "c+e", "multidrop", "van ",
    # senior / experienced (these usually need a CV or experience)
    "manager", "supervisor", "team leader", "engineer", "graduate",
    "analyst", "coordinator", "specialist", "consultant", "director",
    "executive", "head of", "architect", "chef",
]

# Only alert for jobs posted within this many days.
MAX_DAYS_OLD = 3

# Don't call Adzuna more often than this (protects the free usage limit).
MIN_MINUTES_BETWEEN_PULLS = 14

# Start each alert with @everyone so Discord pushes a phone notification.
PING_EVERYONE = True

# At most this many individual alerts per check; extras get one summary line.
MAX_ALERTS_PER_RUN = 12

# ============================================================================
# End of config
# ============================================================================

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/gb/search/1"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alljobs_state.json")
FAILS_BEFORE_WARNING = 4


# ----------------------------------------------------------------------------
# Reading jobs from Adzuna
# ----------------------------------------------------------------------------

def fetch_category(app_id, app_key, category):
    """Fetch recent jobs in one category near WHERE. Returns a list of raw
    job dicts. Raises on a genuine request failure."""
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 50,
        "where": WHERE,
        "distance": DISTANCE_MILES,
        "category": category,
        "sort_by": "date",
        "max_days_old": MAX_DAYS_OLD,
        "what_exclude": "driver driving hgv van",  # server-side pre-filter
        "content-type": "application/json",
    }
    if MIN_HOURLY_PAY > 0:
        # Convert the hourly floor to a yearly one Adzuna understands.
        params["salary_min"] = int(MIN_HOURLY_PAY * ASSUMED_HOURS_PER_WEEK * 52)
    resp = requests.get(ADZUNA_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("results", [])


def fetch_all(app_id, app_key):
    """Fetch every configured category. Returns (jobs, label).
    Succeeds if AT LEAST ONE category responds (even with zero jobs).
    Raises RuntimeError only if every category call fails."""
    jobs = []
    ok = 0
    errors = []
    for category in CATEGORIES:
        try:
            jobs.extend(fetch_category(app_id, app_key, category))
            ok += 1
        except Exception as e:
            errors.append(f"{category}: {type(e).__name__}: {e}")
            print(f"  category failed [{category}]: {type(e).__name__}: {e}")
    if ok == 0:
        raise RuntimeError("all category calls failed:\n  " + "\n  ".join(errors))
    return jobs, f"Adzuna ({ok}/{len(CATEGORIES)} categories)"


def normalise(job):
    loc = (job.get("location") or {}).get("display_name") or "Near London"
    company = (job.get("company") or {}).get("display_name") or "Employer not named"
    cat = (job.get("category") or {}).get("label") or ""
    smin, smax = job.get("salary_min"), job.get("salary_max")
    predicted = str(job.get("salary_is_predicted")) == "1"
    if smin and smax and not predicted:
        pay = f"£{int(smin):,} - £{int(smax):,} / year" if smin != smax else f"£{int(smin):,} / year"
    else:
        pay = "See listing"
    return {
        "id": str(job.get("id") or ""),
        "title": job.get("title") or "Job",
        "company": company,
        "location": loc,
        "category": cat,
        "pay": pay,
        "link": job.get("redirect_url") or "https://www.adzuna.co.uk",
    }


def wanted(job):
    title = job["title"].lower()
    return not any(word in title for word in EXCLUDE_TITLE_WORDS)


# ----------------------------------------------------------------------------
# Discord
# ----------------------------------------------------------------------------

def discord_send(webhook, content=None, embed=None):
    payload = {"allowed_mentions": {"parse": ["everyone"]}}
    if content:
        payload["content"] = content
    if embed:
        payload["embeds"] = [embed]
    try:
        resp = requests.post(webhook, json=payload, timeout=30)
        if resp.status_code == 429:
            time.sleep(min(float(resp.headers.get("Retry-After", "2")), 30))
            resp = requests.post(webhook, json=payload, timeout=30)
        return resp.status_code < 400
    except requests.RequestException as e:
        print(f"Discord send failed: {e}")
        return False


def job_embed(job):
    fields = [
        {"name": "Employer", "value": job["company"], "inline": True},
        {"name": "Location", "value": job["location"], "inline": True},
        {"name": "Pay", "value": job["pay"], "inline": True},
    ]
    if job["category"]:
        fields.append({"name": "Type", "value": job["category"], "inline": True})
    return {
        "title": job["title"],
        "url": job["link"],
        "color": 0x2E86DE,  # blue (Amazon bot uses orange, so alerts look distinct)
        "fields": fields,
        "footer": {"text": "Tap the title to view & apply"},
    }


# ----------------------------------------------------------------------------
# State
# ----------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault("seen", {})
    s.setdefault("fails", 0)
    s.setdefault("fail_alerted", False)
    s.setdefault("announced", False)
    s.setdefault("heartbeat", "")
    s.setdefault("last_pull", "")
    return s


def save_state(s):
    if len(s["seen"]) > 2000:
        newest = sorted(s["seen"].items(), key=lambda kv: kv[1], reverse=True)
        s["seen"] = dict(newest[:2000])
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def minutes_since(iso):
    try:
        then = datetime.fromisoformat(iso)
        return (datetime.now(timezone.utc) - then).total_seconds() / 60
    except (ValueError, TypeError):
        return 1e9  # never pulled -> "a very long time ago"


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_JOBS", "").strip().strip("﻿​").strip()
    app_id = os.environ.get("ADZUNA_APP_ID", "").strip()
    app_key = os.environ.get("ADZUNA_APP_KEY", "").strip()
    if not webhook:
        print("ERROR: DISCORD_WEBHOOK_JOBS is not set.")
        return 1
    if not app_id or not app_key:
        print("ERROR: ADZUNA_APP_ID / ADZUNA_APP_KEY not set.")
        return 1

    state = load_state()
    state["heartbeat"] = date.today().isoformat()

    # Throttle: only actually hit Adzuna every ~15 min, even though the
    # workflow wakes us more often (that keeps the schedule reliable).
    if minutes_since(state["last_pull"]) < MIN_MINUTES_BETWEEN_PULLS:
        print(f"Skipping API call (last pull {minutes_since(state['last_pull']):.0f} min ago).")
        save_state(state)  # heartbeat only changes once/day, so usually no commit
        return 0

    ping = "@everyone " if PING_EVERYONE else ""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        raw, how = fetch_all(app_id, app_key)
    except Exception as e:
        state["fails"] += 1
        print(f"Check FAILED ({state['fails']} consecutive): {e}")
        if state["fails"] >= FAILS_BEFORE_WARNING and not state["fail_alerted"]:
            if discord_send(webhook, content=(
                    f"{ping}⚠️ All-jobs watch: couldn't reach the jobs API for "
                    f"{state['fails']} checks. I'll keep trying and message when "
                    "it recovers.")):
                state["fail_alerted"] = True
        save_state(state)
        return 0

    # Success (this includes "0 jobs found", which is normal).
    state["last_pull"] = datetime.now(timezone.utc).isoformat()
    if state["fail_alerted"]:
        discord_send(webhook, content="✅ All-jobs watch: jobs API is back to normal.")
    state["fails"] = 0
    state["fail_alerted"] = False

    # Dedupe raw results (same job can appear in two categories), then filter.
    seen_ids = set()
    jobs = []
    for j in raw:
        nj = normalise(j)
        if nj["id"] and nj["id"] not in seen_ids:
            seen_ids.add(nj["id"])
            jobs.append(nj)
    matching = [j for j in jobs if wanted(j)]
    new_jobs = [j for j in matching if j["id"] not in state["seen"]]
    print(f"Read OK via {how}: {len(jobs)} jobs, {len(matching)} entry-level, "
          f"{len(new_jobs)} new.")

    # First ever run: announce and quietly remember everything already on the
    # board, so you only get pinged for jobs that appear AFTER setup (no burst).
    if not state["announced"]:
        discord_send(webhook, content=(
            f"{ping}✅ All-jobs watch is live — watching entry-level "
            f"(no-CV, no-driving) jobs within {DISTANCE_MILES} miles of "
            f"{WHERE}. {len(matching)} on the board right now; from here I'll "
            "ping you only when a NEW one appears."))
        for job in matching:
            state["seen"][job["id"]] = today
        state["announced"] = True
        save_state(state)
        return 0

    for job in new_jobs[:MAX_ALERTS_PER_RUN]:
        if discord_send(webhook, content=f"{ping}\U0001f9f0 New job near you!",
                        embed=job_embed(job)):
            state["seen"][job["id"]] = today
            print(f"Alerted: {job['title']} — {job['company']} ({job['location']})")
        time.sleep(1)
    extra = new_jobs[MAX_ALERTS_PER_RUN:]
    if extra:
        if discord_send(webhook, content=(
                f"{ping}➕ ...plus {len(extra)} more new job(s) near you.")):
            for job in extra:
                state["seen"][job["id"]] = today

    save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Unexpected error: {type(e).__name__}: {e}")
        sys.exit(0)
