# TEMPORARY diagnostic v3 - will be deleted after debugging.
# Tests POST-capable free relay proxies from the GitHub runner. We want any
# relay whose own egress IP Amazon allows, that returns real job JSON.
import json
import time
import urllib.parse
import uuid

import requests

GQL = "https://www.jobsatamazon.co.uk/graphql"
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
    "query": "query searchJobCardsByLocation($searchJobRequest: SearchJobRequest!) {\n  searchJobCardsByLocation(searchJobRequest: $searchJobRequest) {\n    nextToken\n    jobCards {\n      jobId\n      jobTitle\n      city\n      postalCode\n      __typename\n    }\n    __typename\n  }\n}\n",
}
BODY_STR = json.dumps(BODY)

def gql_headers():
    return {
        "authorization": "Bearer Status|unauthenticated|Session|",
        "content-type": "application/json",
        "country": "United Kingdom",
        "iscanary": "false",
        "accept": "*/*",
        "accept-language": "en-GB",
        "origin": "https://www.jobsatamazon.co.uk",
        "referer": "https://www.jobsatamazon.co.uk/",
        "user-agent": UA,
        "x-amzn-requestld": str(uuid.uuid4()),
        "x-hvh-time": str(int(time.time() * 1000)),
    }

def verdict(text):
    if "jobCards" in text or "searchJobCardsByLocation" in text:
        return "*** JOB DATA ***"
    low = text.lower()
    if "waf" in low or "403" in text or "forbidden" in low:
        return "blocked(403/WAF)"
    if "error" in low:
        return "error"
    return "other"

def try_relay(name, fn):
    for attempt in (1, 2):
        try:
            r = fn()
            v = verdict(r.text)
            print(f"[{name}] try{attempt}: HTTP {r.status_code} | {v} | {r.text[:110].strip()}")
        except Exception as e:
            print(f"[{name}] try{attempt}: EXC {type(e).__name__}: {str(e)[:90]}")
        time.sleep(1)

# thingproxy: forwards POST + body
try_relay("thingproxy", lambda: requests.post(
    "https://thingproxy.freeboard.io/fetch/" + GQL,
    data=BODY_STR, headers=gql_headers(), timeout=40))

# codetabs: proxy, try POST passthrough
try_relay("codetabs", lambda: requests.post(
    "https://api.codetabs.com/v1/proxy/?quest=" + GQL,
    data=BODY_STR, headers=gql_headers(), timeout=40))

# allorigins raw: try POST
try_relay("allorigins", lambda: requests.post(
    "https://api.allorigins.win/raw?url=" + urllib.parse.quote(GQL, safe=""),
    data=BODY_STR, headers=gql_headers(), timeout=40))

# corsproxy.org
try_relay("corsproxy.org", lambda: requests.post(
    "https://corsproxy.org/?" + urllib.parse.quote(GQL, safe=""),
    data=BODY_STR, headers=gql_headers(), timeout=40))

# cors.eu.org
try_relay("cors.eu.org", lambda: requests.post(
    "https://cors.eu.org/" + GQL,
    data=BODY_STR, headers=gql_headers(), timeout=40))

# proxy.cors.sh
try_relay("cors.sh", lambda: requests.post(
    "https://proxy.cors.sh/" + GQL,
    data=BODY_STR, headers=gql_headers(), timeout=40))

# test.cors.workers.dev (Cloudflare worker relay)
try_relay("cf-worker", lambda: requests.post(
    "https://test.cors.workers.dev/?" + GQL,
    data=BODY_STR, headers=gql_headers(), timeout=40))

print("done")
