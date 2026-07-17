# TEMPORARY diagnostic v2 - will be deleted after debugging.
# Tests alternate free routes to Amazon's job data from a GitHub runner.
import json
import time
import urllib.parse
import uuid

import requests

PROXY_GQL = "https://www.jobsatamazon.co.uk/graphql"
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
        "sec-ch-ua": '"Chromium";v="126", "Not)A;Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": UA,
        "x-amzn-requestld": str(uuid.uuid4()),
        "x-hvh-time": str(int(time.time() * 1000)),
    }

print("runner IP info:")
try:
    print(requests.get("https://ipinfo.io/json", timeout=15).text[:300])
except Exception as e:
    print("EXC:", e)

print("=" * 70)
print("TEST A: proxy graphql with FULL browser header set")
try:
    r = requests.post(PROXY_GQL, json=BODY, headers=gql_headers(), timeout=30)
    print("status:", r.status_code, "| body[:200]:", r.text[:200].replace("\n", " "))
except Exception as e:
    print("EXC:", e)

print("=" * 70)
print("TEST B: graphql POST via corsproxy.io relay")
try:
    target = "https://corsproxy.io/?url=" + urllib.parse.quote(PROXY_GQL, safe="")
    r = requests.post(target, json=BODY, headers=gql_headers(), timeout=45)
    print("status:", r.status_code, "| body[:300]:", r.text[:300].replace("\n", " "))
except Exception as e:
    print("EXC:", e)

print("=" * 70)
print("TEST C: r.jina.ai reader (renders the page from their servers)")
try:
    r = requests.get("https://r.jina.ai/https://www.jobsatamazon.co.uk/app%23/jobSearch",
                     timeout=90, headers={"user-agent": UA})
    print("status:", r.status_code, "| len:", len(r.text))
    print("body[:500]:", r.text[:500].replace("\n", " "))
except Exception as e:
    print("EXC:", e)

print("=" * 70)
print("TEST D: AppSync endpoints direct from runner")
for url in [
    "https://rmr7khwyhzhgpd66fo6ywhjkma.appsync-api.eu-west-1.amazonaws.com/graphql",
    "https://aubvydm7hvgezbr5vteeofwvyq.appsync-api.eu-west-1.amazonaws.com/graphql",
    "https://zal7yl6nnfbw5proqwewrldupe.appsync-api.eu-west-1.amazonaws.com/graphql",
    "https://qy64m4juabaffl7tjakii4gdoa.appsync-api.eu-west-1.amazonaws.com/graphql",
]:
    tag = url.split("//")[1].split(".")[0][:10]
    try:
        r = requests.post(url, json=BODY, headers=gql_headers(), timeout=20)
        print(f"{tag}: HTTP {r.status_code} | {r.text[:120]}".replace("\n", " "))
    except Exception as e:
        print(f"{tag}: EXC {type(e).__name__}")

print("=" * 70)
print("done")
