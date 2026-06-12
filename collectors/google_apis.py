"""
Google Search Console + GA4 collectors (FREE APIs, service-account auth).

Both are free. Setup (one-time):
  1. Create a Google Cloud project; enable "Search Console API" and
     "Google Analytics Data API".
  2. Create a service account, download its JSON key.
  3. Grant the service-account email access:
       - GSC: add it as a user on the property (Search Console > Settings > Users).
       - GA4: add it as a Viewer on the GA4 property (Admin > Property Access).
  4. Set env:
       GOOGLE_APPLICATION_CREDENTIALS = path to the service-account JSON
       GSC_SITE_URL  = e.g. https://www.example.com/  (or sc-domain:example.com)
       GA4_PROPERTY_ID = e.g. 123456789

Usage:
    python google_apis.py --out-gsc ../output/gsc.json --out-ga4 ../output/ga4.json

If credentials are absent, each collector writes a stub marked
{"available": false} so the scorer routes those questions to pending review
instead of failing the run.
"""

import argparse
import json
import os
from datetime import date, timedelta

SCOPES_GSC = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _creds(scopes):
    from google.oauth2 import service_account
    path = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    return service_account.Credentials.from_service_account_file(path, scopes=scopes)


def collect_gsc() -> dict:
    site = os.environ.get("GSC_SITE_URL")
    if not site or not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return {"available": False, "reason": "GSC_SITE_URL / credentials not set"}

    from googleapiclient.discovery import build
    svc = build("searchconsole", "v1", credentials=_creds(SCOPES_GSC),
                cache_discovery=False)

    # Sitemaps: counts + errors
    sitemaps = svc.sitemaps().list(siteUrl=site).execute().get("sitemap", [])
    sm = [{
        "path": s.get("path"),
        "is_pending": s.get("isPending"),
        "errors": int(s.get("errors", 0)),
        "warnings": int(s.get("warnings", 0)),
        "submitted": sum(int(c.get("submitted", 0)) for c in s.get("contents", [])),
        "indexed": sum(int(c.get("indexed", 0)) for c in s.get("contents", [])),
        "last_submitted": s.get("lastSubmitted"),
    } for s in sitemaps]

    # Search analytics (last 28 days) — gives indexed/ranking page count proxy
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=28)
    sa = svc.searchanalytics().query(siteUrl=site, body={
        "startDate": start.isoformat(), "endDate": end.isoformat(),
        "dimensions": ["page"], "rowLimit": 25000,
    }).execute()
    ranking_pages = len(sa.get("rows", []))

    return {
        "available": True,
        "site": site,
        "sitemaps": sm,
        "sitemap_total_errors": sum(s["errors"] for s in sm),
        "sitemap_submitted": sum(s["submitted"] for s in sm),
        "sitemap_indexed": sum(s["indexed"] for s in sm),
        "pages_receiving_impressions_28d": ranking_pages,
    }


def collect_ga4() -> dict:
    prop = os.environ.get("GA4_PROPERTY_ID")
    if not prop or not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return {"available": False, "reason": "GA4_PROPERTY_ID / credentials not set"}

    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest)

    client = BetaAnalyticsDataClient(credentials=_creds(
        ["https://www.googleapis.com/auth/analytics.readonly"]))

    # Which events are firing (confirms event/goal/ecommerce/site-search setup)
    req = RunReportRequest(
        property=f"properties/{prop}",
        date_ranges=[DateRange(start_date="28daysAgo", end_date="today")],
        dimensions=[Dimension(name="eventName")],
        metrics=[Metric(name="eventCount")],
        limit=200,
    )
    resp = client.run_report(req)
    events = {row.dimension_values[0].value: int(row.metric_values[0].value)
              for row in resp.rows}

    ecommerce = any(e in events for e in ("purchase", "add_to_cart", "begin_checkout",
                                          "view_item"))
    site_search = "view_search_results" in events or "search" in events

    return {
        "available": True,
        "property_id": prop,
        "event_names": sorted(events),
        "event_count": len(events),
        "ecommerce_tracking": ecommerce,
        "site_search_tracking": site_search,
        "has_conversions": any(v > 0 for k, v in events.items()
                               if k in ("purchase", "generate_lead", "sign_up")),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-gsc", default="output/gsc.json")
    ap.add_argument("--out-ga4", default="output/ga4.json")
    args = ap.parse_args()

    for fn, out in ((collect_gsc, args.out_gsc), (collect_ga4, args.out_ga4)):
        try:
            data = fn()
        except Exception as e:  # noqa: BLE001 - collectors must never break the run
            data = {"available": False, "error": str(e)}
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"{fn.__name__}: available={data.get('available')} -> {out}")


if __name__ == "__main__":
    main()
