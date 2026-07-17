# TEMPORARY diagnostic - will be deleted after debugging.
# Runs on the GitHub Actions runner and reports exactly how Amazon's site
# responds from a datacenter IP, at each escalation level.
import json
import time
import uuid

import requests

URL = "https://www.jobsatamazon.co.uk/graphql"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BODY = {
    "operationName": "searchJobCardsByLocation",
    "variables": {"searchJobRequest": {
        "locale": "en-GB", "country": "United Kingdom", "keyWords": "",
        "equalFilters": [],
        "containFilters": [{"key": "isPrivateSchedule", "val": ["true", "false"]}],
        "rangeFilters": [], "orFilters": [], "dateFilters": [],
        "sorters": [{"fieldName": "totalPayRateMax", "ascending": "false"}],
        "pageSize": 100, "consolidateSchedule": True}},
    "query": "query searchJobCardsByLocation($searchJobRequest: SearchJobRequest!) {\n  searchJobCardsByLocation(searchJobRequest: $searchJobRequest) {\n    nextToken\n    jobCards {\n      jobId\n      jobTitle\n      city\n      postalCode\n      totalPayRateMinL10N\n      totalPayRateMaxL10N\n      __typename\n    }\n    __typename\n  }\n}\n",
}

def headers():
    return {
        "authorization": "Bearer Status|unauthenticated|Session|",
        "content-type": "application/json",
        "country": "United Kingdom",
        "iscanary": "false",
        "accept": "*/*",
        "accept-language": "en-GB",
        "referer": "https://www.jobsatamazon.co.uk/app",
        "user-agent": UA,
        "x-amzn-requestld": str(uuid.uuid4()),
        "x-hvh-time": str(int(time.time() * 1000)),
    }

print("=" * 70)
print("TEST 1: plain GET of the app page")
try:
    r = requests.get("https://www.jobsatamazon.co.uk/app", timeout=30,
                     headers={"user-agent": UA})
    print("status:", r.status_code, "| server:", r.headers.get("server"),
          "| len:", len(r.text))
except Exception as e:
    print("EXC:", e)

print("=" * 70)
print("TEST 2: direct graphql POST (what the bot does)")
try:
    r = requests.post(URL, json=BODY, headers=headers(), timeout=30)
    print("status:", r.status_code)
    print("resp headers:", json.dumps(dict(r.headers), indent=1)[:1200])
    print("body[:600]:", r.text[:600])
except Exception as e:
    print("EXC:", e)

print("=" * 70)
print("TEST 3: Playwright - load page, log every request, dump page state")
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(user_agent=UA, locale="en-GB")
    page = ctx.new_page()
    reqlog = []

    def on_resp(resp):
        u = resp.url
        if any(k in u for k in ("graphql", "authorize", "amabot", "waf", "captcha", "challenge")):
            body_snip = ""
            try:
                body_snip = resp.text()[:200].replace("\n", " ")
            except Exception:
                body_snip = "<no body>"
            reqlog.append(f"{resp.request.method} {u[:110]} -> {resp.status} | {body_snip}")

    page.on("response", on_resp)
    try:
        page.goto("https://www.jobsatamazon.co.uk/app#/jobSearch",
                  timeout=60000, wait_until="domcontentloaded")
    except Exception as e:
        print("goto EXC:", e)
    page.wait_for_timeout(20000)

    print("--- interesting responses ---")
    for line in reqlog:
        print(line)
    try:
        print("--- cookies ---")
        for c in ctx.cookies():
            print(c["name"], "=", str(c["value"])[:40], "| domain:", c["domain"])
    except Exception as e:
        print("cookie EXC:", e)
    try:
        print("--- visible text[:800] ---")
        print(page.inner_text("body", timeout=5000)[:800])
    except Exception as e:
        print("text EXC:", e)

    print("=" * 70)
    print("TEST 4: fetch from INSIDE the page (browser cookies + TLS)")
    try:
        res = page.evaluate(
            """async (body) => {
                const r = await fetch('/graphql', {
                    method: 'POST',
                    headers: {
                        'authorization': 'Bearer Status|unauthenticated|Session|',
                        'content-type': 'application/json',
                        'country': 'United Kingdom',
                        'iscanary': 'false',
                    },
                    body: JSON.stringify(body),
                });
                const t = await r.text();
                return {status: r.status, body: t.slice(0, 500)};
            }""", BODY)
        print("in-page fetch:", res["status"])
        print("body[:500]:", res["body"])
    except Exception as e:
        print("in-page fetch EXC:", e)

    print("=" * 70)
    print("TEST 5: replay with requests using the browser's cookies")
    try:
        s = requests.Session()
        for c in ctx.cookies():
            s.cookies.set(c["name"], c["value"], domain=c["domain"])
        r = s.post(URL, json=BODY, headers=headers(), timeout=30)
        print("status:", r.status_code, "| body[:300]:", r.text[:300])
    except Exception as e:
        print("EXC:", e)

    browser.close()

print("=" * 70)
print("done")
