#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "curl_cffi>=0.7",
# ]
# ///
"""
efd edge-reputation probe
==========================
Answers ONE question: can the machine this runs on clear the Senate eFD
Akamai edge and get real listing rows back?  Nothing else -- no download,
no OCR, no storage.  Exit 0 = the pipeline is viable from here; exit 1 =
this IP is blocked and you need a proxy (or a different host).

Run locally first (should pass from your laptop), then run it unchanged
from a GitHub Actions runner and compare.
"""
import sys
import time
import random

# curl_cffi impersonates a real Chrome TLS fingerprint; plain requests gets
# 403'd at the edge regardless of headers, so this dependency is the point.
from curl_cffi import requests as cffi_requests

ROOT = "https://efdsearch.senate.gov"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
    # identify yourself politely; harmless if the site ignores it
    "From": "d@verooney.com",
}


def csrf(session):
    return session.cookies.get("csrftoken") or session.cookies.get("csrf")


def main():
    print(">> opening TLS-impersonating session")
    s = cffi_requests.Session(impersonate="chrome")
    s.headers.update(HEADERS)
    s.headers["Referer"] = f"{ROOT}/search/"

    # ---- step 1: landing page (sets csrftoken cookie + shows agreement gate)
    print(">> GET /search/home/  (expect 200; a 403 here == edge block)")
    r = s.get(f"{ROOT}/search/home/", timeout=60)
    print(f"   status={r.status_code}  server={r.headers.get('Server')}")
    if r.status_code in (401, 403):
        body = " ".join(r.text[:200].split())
        print(f"!! BLOCKED at edge. body snippet: {body}")
        print("!! This IP cannot clear Akamai. Verdict: need a proxy.")
        return 1
    if r.status_code != 200:
        print(f"!! Unexpected status {r.status_code}. Verdict: investigate.")
        return 1

    token = csrf(s)
    print(f"   csrftoken acquired: {(token or '')[:8]}...")

    # ---- step 2: accept the prohibition-agreement gate
    print(">> POST /search/home/  (accept terms)")
    s.post(f"{ROOT}/search/home/",
           data={"csrfmiddlewaretoken": token, "prohibition_agreement": "1"},
           headers={"Referer": f"{ROOT}/search/home/"}, timeout=60)

    time.sleep(2 + random.uniform(0, 0.8))  # stay polite

    # ---- step 3: one real listing call -- proves data actually flows
    print(">> POST /search/report/data/  (one page of results)")
    payload = {
        "draw": "1", "start": "0", "length": "25",
        "report_types": "[]", "filer_types": "[]",
        "submitted_start_date": "01/01/2024 00:00:00",
        "submitted_end_date": "", "candidate_state": "",
        "senator_state": "", "office_id": "",
        "first_name": "", "last_name": "",
    }
    r = s.post(f"{ROOT}/search/report/data/", data=payload,
               headers={"X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": csrf(s),
                        "Referer": f"{ROOT}/search/"}, timeout=60)
    print(f"   status={r.status_code}")
    if r.status_code != 200:
        print("!! Listing call failed even though the edge let us in.")
        print("!! Verdict: handshake OK but payload/endpoint drifted -- inspect.")
        return 1

    rows = r.json().get("data", [])
    print(f"   rows returned: {len(rows)}")
    if not rows:
        print("?? Zero rows. Edge cleared but no data -- widen the date range")
        print("?? or the payload field names may have changed. Not an IP block.")
        return 1

    print("\n== PASS ==  This host cleared the edge and got live rows.")
    print("== The pipeline is viable from this IP. ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
