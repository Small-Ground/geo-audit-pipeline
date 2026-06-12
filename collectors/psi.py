"""
PageSpeed Insights collector (FREE).

The PSI API returns both Lighthouse lab data and CrUX field data, covering
nearly the entire Site Speed tab + the Content Lighthouse-accessibility row.
Free, no billing — just an API key from https://developers.google.com/speed/docs/insights/v5/get-started

Usage:
    python psi.py https://www.example.com/ --out ../output/psi.json
Env:
    PSI_API_KEY   Google API key with PageSpeed Insights API enabled
"""

import argparse
import json
import os
import sys

import httpx

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def run(url: str, strategy: str, api_key: str) -> dict:
    params = [("url", url), ("strategy", strategy),
              ("category", "performance"), ("category", "accessibility"),
              ("category", "seo"), ("category", "best-practices")]
    if api_key:
        params.append(("key", api_key))
    r = httpx.get(ENDPOINT, params=params, timeout=90)
    r.raise_for_status()
    data = r.json()

    lh = data.get("lighthouseResult", {})
    audits = lh.get("audits", {})
    cats = lh.get("categories", {})

    def metric(audit_id):
        return audits.get(audit_id, {}).get("numericValue")

    def score(cat):
        s = cats.get(cat, {}).get("score")
        return round(s * 100) if s is not None else None

    # CrUX field data (real-user, 75th percentile)
    crux = data.get("loadingExperience", {}).get("metrics", {})

    def field_p75(key):
        return crux.get(key, {}).get("percentile")

    return {
        "strategy": strategy,
        "performance_score": score("performance"),
        "accessibility_score": score("accessibility"),
        "seo_score": score("seo"),
        "best_practices_score": score("best-practices"),
        "lab": {
            "lcp_ms": metric("largest-contentful-paint"),
            "fcp_ms": metric("first-contentful-paint"),
            "cls": metric("cumulative-layout-shift"),
            "tti_ms": metric("interactive"),
            "tbt_ms": metric("total-blocking-time"),
            "speed_index_ms": metric("speed-index"),
            "uses_text_compression": audits.get("uses-text-compression", {}).get("score"),
            "uses_long_cache_ttl": audits.get("uses-long-cache-ttl", {}).get("score"),
            "render_blocking": audits.get("render-blocking-resources", {}).get("score"),
            "unminified_css": audits.get("unminified-css", {}).get("score"),
            "unminified_js": audits.get("unminified-javascript", {}).get("score"),
            "offscreen_images": audits.get("offscreen-images", {}).get("score"),
            "modern_image_formats": audits.get("modern-image-formats", {}).get("score"),
        },
        "field_p75": {
            "lcp_ms": field_p75("LARGEST_CONTENTFUL_PAINT_MS"),
            "fcp_ms": field_p75("FIRST_CONTENTFUL_PAINT_MS"),
            "cls": (field_p75("CUMULATIVE_LAYOUT_SHIFT_SCORE") or 0) / 100,
            "inp_ms": field_p75("INTERACTION_TO_NEXT_PAINT"),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default="output/psi.json")
    args = ap.parse_args()

    api_key = os.environ.get("PSI_API_KEY", "")
    if not api_key:
        print("WARNING: PSI_API_KEY not set — using unauthenticated quota "
              "(low rate limit).", file=sys.stderr)

    result = {"url": args.url}
    for strategy in ("mobile", "desktop"):
        try:
            result[strategy] = run(args.url, strategy, api_key)
            s = result[strategy]
            print(f"{strategy}: perf={s['performance_score']} "
                  f"a11y={s['accessibility_score']} "
                  f"LCP(lab)={s['lab']['lcp_ms']}ms CLS={s['lab']['cls']}")
        except httpx.HTTPError as e:
            result[strategy] = {"error": str(e)}
            print(f"{strategy}: ERROR {e}", file=sys.stderr)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
