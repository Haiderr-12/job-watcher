# TEMPORARY diagnostic v4 - will be deleted after debugging.
# Confirm cors.sh reliability and find a 2nd working relay for redundancy.
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
    "query": "query searchJobCardsByLocation($searchJobRequest: SearchJobRequest!) {\n  searchJobCardsByLocation(searchJobRequest: $searchJobRequest) {\n    nextToken\n    jobCards { jobId jobTitle city postalCode __typename }\n    __typename\n  }\n}\n",
}
BODY_STR = json.dumps(BODY)

def hdr(extra=None):
    h = {
        "authorization": "Bearer Status|unauthenticated|Session|",
        "content-type": "application/json",
        "country": "United Kingdom",
        "iscanary": "false",
        "accept": "*/*",
        "accept-language": "en-GB",
        "user-agent": UA,
        "x-amzn-requestld": str(uuid.uuid4()),
        "x-hvh-time": str(int(time.time() * 1000)),
    }
    if extra:
        h.update(extra)
    return h

def verdict(text):
    if "jobCards" in text:
        return "*** JOB DATA ***"
    low = text.lower()
    if "waf" in low or "forbidden" in low or "403" in text:
        return "blocked"
    return "other:" + text[:60].replace("\n", " ")

def run(name, fn):
    ok = 0
    for attempt in range(1, 6):
        try:
            r = fn()
            v = verdict(r.text)
            if "JOB DATA" in v:
                ok += 1
            print(f"[{name}] {attempt}: HTTP {r.status_code} | {v}")
        except Exception as e:
            print(f"[{name}] {attempt}: EXC {type(e).__name__}: {str(e)[:70]}")
        time.sleep(2)
    print(f"[{name}] SUCCESS {ok}/5")

# cors.sh WITH origin header (5x to check for bans under repeated use)
run("cors.sh+origin", lambda: requests.post(
    "https://proxy.cors.sh/" + GQL, data=BODY_STR,
    headers=hdr({"origin": "https://www.jobsatamazon.co.uk"}), timeout=40))

# cors.sh WITHOUT origin header
run("cors.sh-bare", lambda: requests.post(
    "https://proxy.cors.sh/" + GQL, data=BODY_STR, headers=hdr(), timeout=40))

# cors.lol
run("cors.lol", lambda: requests.post(
    "https://api.cors.lol/?url=" + urllib.parse.quote(GQL, safe=""),
    data=BODY_STR, headers=hdr(), timeout=40))

# corsfix
run("corsfix", lambda: requests.post(
    "https://proxy.corsfix.com/?" + GQL, data=BODY_STR,
    headers=hdr({"origin": "https://www.jobsatamazon.co.uk"}), timeout=40))

# allorigins /raw GET-style but POST body (retest, servers may be up now)
run("allorigins", lambda: requests.post(
    "https://api.allorigins.win/raw?url=" + urllib.parse.quote(GQL, safe=""),
    data=BODY_STR, headers=hdr(), timeout=40))

print("done")
