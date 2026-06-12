"""
Deterministic offline rules — the no-LLM fallback for the scorer.

These let the pipeline produce a real, populated audit + weighted score
WITHOUT any LLM/API key, by mapping crawler/PSI evidence straight to Yes/No
for the high-signal, unambiguous checks. The production path is the LLM
(see score.py); this is what runs under `--offline`.

Each rule: a substring matched against the question (lowercased) -> a function
(crawl_agg, site, psi) -> "Yes" | "No" | "N/A" | None (None = can't decide).
"Yes" always means PASS.
"""


def yn(passed) -> str:
    return "Yes" if passed else "No"


def psi_metric(psi, strategy, group, key):
    try:
        return psi[strategy][group][key]
    except (KeyError, TypeError):
        return None


RULES = [
    # ---- Technical -----------------------------------------------------
    ("no pages returning a 404", lambda a, s, p: yn(a["status_404"] == 0)),
    ("returning a server error", lambda a, s, p: yn(a["status_5xx"] == 0)),
    ("permanent redirects using a 301", lambda a, s, p:
        yn(a["redirect_non301"] == 0) if a["redirect_count"] else "N/A"),
    ("avoid using meta refresh", lambda a, s, p: yn(a["meta_refresh_pages"] == 0)),
    ("avoid redirect chains", lambda a, s, p: None),
    ("served outside of iframes", lambda a, s, p: yn(a["pages_with_iframe"] == 0)),
    ("avoid using flash", lambda a, s, p: yn(a["pages_with_flash"] == 0)),
    ("breadcrumbs are used", lambda a, s, p:
        yn(a["pages_with_breadcrumb_schema"] > 0)),
    ("open graph and twitter card", lambda a, s, p:
        yn(a["pages_with_og"] > 0 and a["pages_with_twitter"] > 0)),
    ('rel="me"', lambda a, s, p: yn(a["rel_me_present"])),
    ("link to the organisation/brand social", lambda a, s, p:
        yn(a["social_links_present"])),

    # ---- Site Speed (PSI) ---------------------------------------------
    ("no longer than 3 seconds", lambda a, s, p: yn(a["avg_load_s"] <= 3)),
    ("largest contentful paint", lambda a, s, p:
        (lambda v: yn(v <= 2500) if v is not None else "N/A")(
            psi_metric(p, "mobile", "lab", "lcp_ms"))),
    ("first contentful paint", lambda a, s, p:
        (lambda v: yn(v <= 1800) if v is not None else "N/A")(
            psi_metric(p, "mobile", "lab", "fcp_ms"))),
    ("cumulative layout shift", lambda a, s, p:
        (lambda v: yn(v <= 0.1) if v is not None else "N/A")(
            psi_metric(p, "mobile", "lab", "cls"))),
    ("time to interactive", lambda a, s, p:
        (lambda v: yn(v <= 3500) if v is not None else "N/A")(
            psi_metric(p, "mobile", "lab", "tti_ms"))),
    ("desktop site satisfy google page speed", lambda a, s, p:
        (lambda v: yn(v >= 80) if v is not None else "N/A")(
            psi_metric(p, "desktop", None, "performance_score") if False else
            (p.get("desktop", {}).get("performance_score")))),
    ("mobile site satisfy google page speed", lambda a, s, p:
        (lambda v: yn(v >= 80) if v is not None else "N/A")(
            p.get("mobile", {}).get("performance_score"))),
    ("server-side compression", lambda a, s, p:
        (lambda v: yn(v == 1) if v is not None else "N/A")(
            psi_metric(p, "mobile", "lab", "uses_text_compression"))),
    ("images less than 100kb", lambda a, s, p: yn(a["images_over_100kb"] == 0)),
    ("http2 been implemented", lambda a, s, p: yn(a["http2"])),

    # ---- Accessibility -------------------------------------------------
    ("implemented https protocol", lambda a, s, p: yn(s["https"])),
    ("utilise a robots.txt", lambda a, s, p: yn(s["robots_txt"]["exists"])),
    ("allow access to javascript", lambda a, s, p:
        yn(not s["robots_txt"].get("blocks_js"))),
    ("allow access to css", lambda a, s, p:
        yn(not s["robots_txt"].get("blocks_css"))),
    ("robots.txt correctly reference the sitemap", lambda a, s, p:
        yn(s["robots_txt"].get("references_sitemap"))),
    ("implemented an xml sitemap", lambda a, s, p: yn(s["sitemap"]["exists"])),
    ("sitemap <lastmod> dates", lambda a, s, p:
        yn(s["sitemap"].get("has_lastmod")) if s["sitemap"]["exists"] else "N/A"),
    ("noindex tags on important pages", lambda a, s, p:
        yn(a["noindex_pages"] == 0)),
    ("non-default protocol", lambda a, s, p:
        yn(s["redirect_tests"].get("http", {}).get("redirects"))),
    ("non-www", lambda a, s, p:
        yn(s["redirect_tests"].get("non_www", {}).get("redirects"))),

    # ---- Content / Architecture (crawler) ------------------------------
    ("unique and optimised title tag", lambda a, s, p:
        yn(a["dup_title"] == 0 and a["title_below_30"] == 0)),
    ("optimised meta descriptions", lambda a, s, p: yn(a["missing_meta"] == 0)),
    ("single h1 on each core page", lambda a, s, p: yn(a["multiple_h1"] == 0)),
    ("avoid thin content", lambda a, s, p: yn(a["thin_pages"] == 0)),
    ("avoid hiding content within accordions", lambda a, s, p: None),
    ("urls on the site absolute", lambda a, s, p: None),
    ("not any orphaned pages", lambda a, s, p: yn(a["orphan_pages"] == 0)),
    ("orphaned pages", lambda a, s, p: yn(a["orphan_pages"] == 0)),
    ("crawl depth to key content 3 clicks", lambda a, s, p:
        yn(a["avg_depth"] <= 3)),
    ("descriptive anchor text", lambda a, s, p:
        yn(a["generic_anchor_pages"] == 0)),
    ('click here', lambda a, s, p: yn(a["generic_anchor_pages"] == 0)),
    ("mobile-friendly version", lambda a, s, p: yn(a["pages_with_viewport"] > 0)),

    # ---- Analytics (crawler tag detection) -----------------------------
    ("using google analytics or any other analytics", lambda a, s, p:
        yn(a["pages_with_analytics"] > 0)),
    ("tracking code implemented across all pages", lambda a, s, p:
        yn(a["pages_with_analytics"] == a["pages_html"])),
    ("tag management solution", lambda a, s, p: None),
]


def evaluate(question: str, crawl_agg: dict, site: dict, psi: dict):
    q = question.lower()
    for needle, fn in RULES:
        if needle in q:
            try:
                return fn(crawl_agg, site, psi or {})
            except (KeyError, TypeError, ZeroDivisionError):
                return None
    return None
