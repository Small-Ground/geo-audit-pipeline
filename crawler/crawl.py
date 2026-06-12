"""
Free-tier crawler — the Screaming Frog replacement for the GEO audit pipeline.

BFS-crawls a domain and extracts the on-page / technical signals every
"crawler"-sourced question in config/question_bank.json depends on, plus
site-level signals (robots.txt, sitemap, protocol/www/slash redirect tests).

Output: a single crawl.json with per-page records, site signals, and the
aggregates the scorer feeds to LiteLLM. Designed to run headless in a Docker
sidecar (see docker-compose.yml) writing to a shared volume.

Usage:
    python crawl.py https://www.example.com/ --max-pages 300 --out ../output/crawl.json
"""

import argparse
import asyncio
import json
import re
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag

import httpx
from bs4 import BeautifulSoup

UA = "GEO-Audit-Bot/0.1 (+prototype)"
# Fields re-derived from the rendered DOM when --render is on. Technical signals
# (status, redirects, http_version) stay from the httpx response — rendering
# follows redirects and would hide them.
RENDER_FIELDS = [
    "title", "title_len", "meta_description", "meta_desc_len", "h1", "h1_count",
    "h2_count", "hreflang", "has_x_default", "schema", "open_graph",
    "twitter_card", "rel_me_or_sameas", "social_links", "word_count",
    "text_sample", "content_in_accordion", "last_updated_visible",
    "click_to_call", "has_iframe", "img_count", "img_missing_alt", "img_urls",
    "internal_links", "external_links", "anchors", "generic_anchors",
]
SCHEMA_TYPES = [
    "Product", "Offer", "AggregateRating", "Review", "Article", "NewsArticle",
    "FAQPage", "HowTo", "HowToStep", "Organization", "WebSite",
    "BreadcrumbList",
]
SOCIAL_HOSTS = ("facebook.com", "instagram.com", "twitter.com", "x.com",
                "linkedin.com", "youtube.com", "pinterest.com", "tiktok.com")
IMG_SIZE_LIMIT = 100 * 1024  # 100 kb threshold from the workbook


def norm(url: str) -> str:
    url, _ = urldefrag(url)
    return url.rstrip("/") if url.endswith("/") and urlparse(url).path != "/" else url


def same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.replace("www.", "") == urlparse(b).netloc.replace("www.", "")


def text_of(soup: BeautifulSoup) -> str:
    # non-mutating: do NOT extract() nodes — that would strip JSON-LD <script>
    # tags from the shared soup before schema detection runs.
    parts = [t for t in soup.find_all(string=True)
             if t.parent.name not in ("script", "style", "noscript")]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def parse_page(url: str, html: str, status: int, elapsed: float,
               http_version: str, headers: dict) -> dict:
    soup = BeautifulSoup(html, "lxml")
    body_text = text_of(soup)

    title = (soup.title.string or "").strip() if soup.title else ""
    meta_desc = ""
    md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if md and md.get("content"):
        meta_desc = md["content"].strip()

    robots_meta = ""
    rm = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    if rm and rm.get("content"):
        robots_meta = rm["content"].lower()

    canonical = ""
    cl = soup.find("link", rel=lambda v: v and "canonical" in v)
    if cl and cl.get("href"):
        canonical = cl["href"]

    h1s = [h.get_text(strip=True) for h in soup.find_all("h1")]

    # hreflang
    hreflang = [l.get("hreflang", "") for l in soup.find_all("link", rel="alternate")
                if l.get("hreflang")]

    # images
    imgs = soup.find_all("img")
    img_urls = [urljoin(url, i["src"]) for i in imgs if i.get("src")]
    missing_alt = sum(1 for i in imgs if not (i.get("alt") or "").strip())

    # links
    links, anchors = [], []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"])
        if href.startswith("http"):
            links.append(href)
            anchors.append((a.get_text(strip=True) or "").lower())

    # JSON-LD schema types
    schema_found = set()
    for s in soup.find_all("script", type=re.compile(r"application/ld\+json", re.I)):
        raw = s.get_text() or ""          # get_text() is robust where .string is None
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        stack = list(data) if isinstance(data, list) else [data]
        while stack:                       # walk @graph and nested arrays
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            elif isinstance(obj, dict):
                if isinstance(obj.get("@graph"), list):
                    stack.extend(obj["@graph"])
                t = obj.get("@type", "")
                for tt in (t if isinstance(t, list) else [t]):
                    if tt in SCHEMA_TYPES:
                        schema_found.add(tt)

    # social / markup signals
    og = bool(soup.find("meta", property=re.compile("^og:", re.I)))
    twitter = bool(soup.find("meta", attrs={"name": re.compile("^twitter:", re.I)}))
    rel_me = bool(soup.find("a", rel="me")) or "sameas" in html.lower()
    social_links = sorted({h for ln in links for h in SOCIAL_HOSTS if h in ln})

    # misc technical
    meta_refresh = bool(soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)}))
    has_iframe = bool(soup.find("iframe"))
    has_flash = ".swf" in html.lower()
    has_viewport = bool(soup.find("meta", attrs={"name": "viewport"}))
    click_to_call = bool(soup.find("a", href=re.compile(r"^tel:", re.I)))
    in_accordion = bool(soup.select('[class*="accordion"], [class*="tab-"], details'))
    last_updated = bool(re.search(r"last updated|updated on", html, re.I))

    # analytics / tag detection
    h_low = html.lower()
    has_ga4 = "gtag(" in h_low or "g-" in h_low and "googletagmanager.com/gtag" in h_low
    has_gtm = "googletagmanager.com/gtm" in h_low or "gtm-" in h_low
    has_analytics = has_ga4 or has_gtm or "google-analytics.com" in h_low

    return {
        "url": url,
        "status": status,
        "elapsed_s": round(elapsed, 3),
        "http_version": http_version,
        "title": title,
        "title_len": len(title),
        "meta_description": meta_desc,
        "meta_desc_len": len(meta_desc),
        "robots_meta": robots_meta,
        "noindex": "noindex" in robots_meta,
        "x_robots": headers.get("x-robots-tag", ""),
        "canonical": canonical,
        "h1": h1s,
        "h1_count": len(h1s),
        "h2_count": len(soup.find_all("h2")),
        "hreflang": hreflang,
        "has_x_default": "x-default" in hreflang,
        "img_count": len(imgs),
        "img_missing_alt": missing_alt,
        "img_urls": img_urls[:40],
        "word_count": len(body_text.split()),
        "text_sample": body_text[:1500],
        "internal_links": [l for l in links if same_host(l, url)],
        "external_links": [l for l in links if not same_host(l, url)],
        "anchors": anchors[:60],
        "generic_anchors": sum(1 for a in anchors if a in ("click here", "read more", "more")),
        "schema": sorted(schema_found),
        "open_graph": og,
        "twitter_card": twitter,
        "rel_me_or_sameas": rel_me,
        "social_links": social_links,
        "meta_refresh": meta_refresh,
        "has_iframe": has_iframe,
        "has_flash": has_flash,
        "has_viewport": has_viewport,
        "click_to_call": click_to_call,
        "content_in_accordion": in_accordion,
        "last_updated_visible": last_updated,
        "has_ga4": has_ga4,
        "has_gtm": has_gtm,
        "has_analytics": has_analytics,
    }


async def fetch(client, url):
    t0 = time.perf_counter()
    r = await client.get(url, follow_redirects=False)
    elapsed = time.perf_counter() - t0
    return r, elapsed


async def site_signals(client, start_url):
    p = urlparse(start_url)
    base = f"{p.scheme}://{p.netloc}"
    sig = {"https": p.scheme == "https"}

    # robots.txt
    try:
        r = await client.get(f"{base}/robots.txt", follow_redirects=True)
        body = r.text if r.status_code == 200 else ""
        sig["robots_txt"] = {
            "exists": r.status_code == 200,
            "references_sitemap": "sitemap:" in body.lower(),
            "blocks_js": bool(re.search(r"disallow:.*\.js", body, re.I)),
            "blocks_css": bool(re.search(r"disallow:.*\.css", body, re.I)),
            "sitemaps": re.findall(r"(?i)sitemap:\s*(\S+)", body),
        }
    except httpx.HTTPError:
        sig["robots_txt"] = {"exists": False}

    # sitemap
    sm_url = (sig["robots_txt"].get("sitemaps") or [f"{base}/sitemap.xml"])[0]
    try:
        r = await client.get(sm_url, follow_redirects=True)
        body = r.text if r.status_code == 200 else ""
        sig["sitemap"] = {
            "url": sm_url,
            "exists": r.status_code == 200,
            "url_count": body.count("<loc>"),
            "has_lastmod": "<lastmod>" in body.lower(),
        }
    except httpx.HTTPError:
        sig["sitemap"] = {"exists": False}

    # redirect / canonicalisation tests on homepage variants
    variants = {
        "http": f"http://{p.netloc}/",
        "non_www": f"{p.scheme}://{p.netloc.replace('www.', '')}/" if "www." in p.netloc
        else f"{p.scheme}://www.{p.netloc}/",
        "uppercase_path": f"{base}/HOME-TEST-CANONICAL",
        "trailing_slash": f"{base}/?probe=1",
    }
    tests = {}
    for name, vurl in variants.items():
        try:
            r = await client.get(vurl, follow_redirects=False)
            tests[name] = {"status": r.status_code,
                           "redirects": r.status_code in (301, 302, 307, 308),
                           "location": r.headers.get("location", "")}
        except httpx.HTTPError:
            tests[name] = {"status": None}
    sig["redirect_tests"] = tests
    return sig


async def sitemap_seed_urls(client, sitemap_url, cap):
    """Expand the sitemap (incl. sitemap-index) and return an even sample of
    real URLs, so the crawl actually visits PDPs/categories/blog pages rather
    than only what's reachable from the homepage."""
    if not sitemap_url or cap <= 0:
        return []
    try:
        body = (await client.get(sitemap_url, follow_redirects=True)).text
    except httpx.HTTPError:
        return []
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", body)
    # sitemap index -> fetch children
    if "<sitemapindex" in body.lower() or (locs and all(l.endswith(".xml") for l in locs[:3])):
        children, locs = locs[:8], []
        for child in children:
            try:
                cb = (await client.get(child, follow_redirects=True)).text
                locs += re.findall(r"<loc>\s*(.*?)\s*</loc>", cb)
            except httpx.HTTPError:
                pass
    locs = [l for l in locs if l.startswith("http") and not l.endswith(".xml")]
    if len(locs) > cap:                       # even spread across the sitemap
        step = max(1, len(locs) // cap)
        locs = locs[::step][:cap]
    return locs


async def render_pages(pages, cap):
    """Re-read crawled pages through a headless browser so JS-injected schema,
    hreflang, OG tags and content are captured. Updates page dicts in place."""
    from playwright.async_api import async_playwright

    targets = [p for p in pages
               if "title" in p and not p.get("error") and not p.get("non_html")][:cap]
    if not targets:
        return 0
    rendered = 0
    sem = asyncio.Semaphore(4)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        ctx = await browser.new_context(user_agent=UA)

        async def render_one(p):
            nonlocal rendered
            async with sem:
                page = await ctx.new_page()
                html = None
                try:
                    await page.goto(p["url"], wait_until="load", timeout=30000)
                    await page.wait_for_timeout(1200)  # let late JSON-LD/JS settle
                    html = await page.content()
                except Exception:  # noqa: BLE001 - skip pages that fail to render
                    html = None
                finally:
                    await page.close()
            if html:
                parsed = parse_page(p["url"], html, p.get("status"),
                                    p.get("elapsed_s", 0), p.get("http_version", ""), {})
                for k in RENDER_FIELDS:
                    p[k] = parsed[k]
                p["rendered"] = True
                rendered += 1

        await asyncio.gather(*(render_one(p) for p in targets))
        await browser.close()
    return rendered


async def head_image_sizes(client, urls, cap):
    sizes, sem = {}, asyncio.Semaphore(20)

    async def one(u):
        async with sem:
            try:
                r = await client.head(u, follow_redirects=True)
                cl = r.headers.get("content-length")
                if cl:
                    sizes[u] = int(cl)
            except (httpx.HTTPError, ValueError):
                pass
    await asyncio.gather(*(one(u) for u in list(urls)[:cap]))
    return sizes


async def crawl(start_url, max_pages, img_cap, render=False, render_cap=30,
                sitemap_seed=60):
    start_url = norm(start_url)
    seen, pages, depth = {start_url}, [], {start_url: 0}
    queue = deque([start_url])
    redirects = []

    limits = httpx.Limits(max_connections=20)
    async with httpx.AsyncClient(http2=True, timeout=20, limits=limits,
                                 headers={"User-Agent": UA}) as client:
        site = await site_signals(client, start_url)

        # seed the queue with a sample of real sitemap URLs (PDPs/categories/blog)
        seeded = set()
        for u in await sitemap_seed_urls(client, site.get("sitemap", {}).get("url"), sitemap_seed):
            nu = norm(u)
            if nu not in seen:
                seen.add(nu)
                depth[nu] = 1
                seeded.add(nu)
                queue.append(nu)

        while queue and len(pages) < max_pages:
            url = queue.popleft()
            try:
                r, elapsed = await fetch(client, url)
            except httpx.HTTPError:
                pages.append({"url": url, "status": None, "error": True})
                continue

            if r.status_code in (301, 302, 303, 307, 308):
                loc = urljoin(url, r.headers.get("location", ""))
                redirects.append({"from": url, "to": loc, "status": r.status_code})
                nloc = norm(loc)
                if same_host(nloc, start_url) and nloc not in seen and len(seen) < max_pages * 3:
                    seen.add(nloc)
                    depth[nloc] = depth.get(url, 0)
                    queue.append(nloc)
                continue

            ctype = r.headers.get("content-type", "")
            if "text/html" not in ctype:
                pages.append({"url": url, "status": r.status_code, "non_html": True})
                continue

            page = parse_page(url, r.text, r.status_code, elapsed,
                              r.http_version, dict(r.headers))
            page["depth"] = depth.get(url, 0)
            page["seeded"] = url in seeded
            pages.append(page)

            if depth.get(url, 0) < 8:
                for link in page["internal_links"]:
                    nl = norm(link)
                    if nl not in seen and len(seen) < max_pages * 3:
                        seen.add(nl)
                        depth[nl] = depth.get(url, 0) + 1
                        queue.append(nl)

        # optional: re-read pages through a headless browser (JS-rendered sites)
        n_rendered = 0
        if render:
            n_rendered = await render_pages(pages, render_cap)

        # image sizes (sampled across crawled pages; after render so JS imgs count)
        all_imgs = {u for pg in pages for u in pg.get("img_urls", [])}
        img_sizes = await head_image_sizes(client, all_imgs, img_cap)

    return assemble(start_url, pages, site, redirects, depth, img_sizes, n_rendered)


def assemble(start_url, pages, site, redirects, depth, img_sizes, n_rendered=0):
    html_pages = [p for p in pages if p.get("title") is not None and "error" not in p
                  and not p.get("non_html")]

    # orphan detection: pages discovered but never linked-to internally
    linked_to = {norm(l) for p in html_pages for l in p.get("internal_links", [])}
    orphans = [p["url"] for p in html_pages if norm(p["url"]) not in linked_to
               and p["url"] != start_url and not p.get("seeded")]

    titles = [p["title"] for p in html_pages if p.get("title")]
    dup_titles = len(titles) - len(set(titles))
    metas = [p["meta_description"] for p in html_pages if p.get("meta_description")]
    dup_metas = len(metas) - len(set(metas))
    h1_flat = [tuple(p.get("h1", [])) for p in html_pages]

    big_imgs = {u: s for u, s in img_sizes.items() if s > IMG_SIZE_LIMIT}

    agg = {
        "pages_html": len(html_pages),
        "status_404": sum(1 for p in pages if p.get("status") == 404),
        "status_5xx": sum(1 for p in pages if (p.get("status") or 0) >= 500),
        "redirect_count": len(redirects),
        "redirect_301": sum(1 for r in redirects if r["status"] == 301),
        "redirect_non301": sum(1 for r in redirects if r["status"] != 301),
        "meta_refresh_pages": sum(1 for p in html_pages if p.get("meta_refresh")),
        "missing_title": sum(1 for p in html_pages if not p.get("title")),
        "dup_title": dup_titles,
        "title_below_30": sum(1 for p in html_pages if 0 < p.get("title_len", 0) < 30),
        "missing_meta": sum(1 for p in html_pages if not p.get("meta_description")),
        "dup_meta": dup_metas,
        "missing_h1": sum(1 for p in html_pages if p.get("h1_count", 0) == 0),
        "multiple_h1": sum(1 for p in html_pages if p.get("h1_count", 0) > 1),
        "dup_h1": len(h1_flat) - len(set(h1_flat)),
        "noindex_pages": sum(1 for p in html_pages if p.get("noindex")),
        "missing_alt_total": sum(p.get("img_missing_alt", 0) for p in html_pages),
        "images_over_100kb": len(big_imgs),
        "images_checked": len(img_sizes),
        "thin_pages": sum(1 for p in html_pages if 0 < p.get("word_count", 0) < 300),
        "orphan_pages": len(orphans),
        "max_depth": max(depth.values()) if depth else 0,
        "avg_depth": round(sum(p.get("depth", 0) for p in html_pages) / max(len(html_pages), 1), 2),
        "pages_rendered": n_rendered,
        "pages_with_schema": sum(1 for p in html_pages if p.get("schema")),
        "schema_types_seen": sorted({t for p in html_pages for t in p.get("schema", [])}),
        "pages_with_og": sum(1 for p in html_pages if p.get("open_graph")),
        "pages_with_twitter": sum(1 for p in html_pages if p.get("twitter_card")),
        "pages_with_breadcrumb_schema": sum(1 for p in html_pages if "BreadcrumbList" in p.get("schema", [])),
        "pages_with_analytics": sum(1 for p in html_pages if p.get("has_analytics")),
        "pages_with_viewport": sum(1 for p in html_pages if p.get("has_viewport")),
        "pages_with_flash": sum(1 for p in html_pages if p.get("has_flash")),
        "pages_with_iframe": sum(1 for p in html_pages if p.get("has_iframe")),
        "generic_anchor_pages": sum(1 for p in html_pages if p.get("generic_anchors", 0) > 0),
        "social_links_present": any(p.get("social_links") for p in html_pages),
        "rel_me_present": any(p.get("rel_me_or_sameas") for p in html_pages),
        "avg_load_s": round(sum(p.get("elapsed_s", 0) for p in html_pages) / max(len(html_pages), 1), 3),
        "pages_over_3s": sum(1 for p in html_pages if p.get("elapsed_s", 0) > 3),
        "http2": any(p.get("http_version") == "HTTP/2" for p in html_pages),
        "all_https": all(p["url"].startswith("https") for p in html_pages),
    }

    # keep a representative sample of full page records for the LLM judge
    sample = sorted(html_pages, key=lambda p: p.get("depth", 99))[:25]

    return {
        "start_url": start_url,
        "domain": urlparse(start_url).netloc,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "pages_crawled": len(pages),
        "site": site,
        "aggregates": agg,
        "orphan_sample": orphans[:25],
        "redirect_sample": redirects[:25],
        "page_sample": sample,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start_url")
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--img-cap", type=int, default=300)
    ap.add_argument("--render", action="store_true",
                    help="re-read pages via headless Chromium (JS-rendered sites)")
    ap.add_argument("--render-cap", type=int, default=30,
                    help="max pages to render when --render is set")
    ap.add_argument("--sitemap-seed", type=int, default=60,
                    help="sample N real URLs from the sitemap into the crawl (0=off)")
    ap.add_argument("--out", default="output/crawl.json")
    args = ap.parse_args()

    result = asyncio.run(crawl(args.start_url, args.max_pages, args.img_cap,
                               render=args.render, render_cap=args.render_cap,
                               sitemap_seed=args.sitemap_seed))

    import os
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    a = result["aggregates"]
    print(f"Crawled {result['pages_crawled']} pages "
          f"({a['pages_html']} HTML, {a['pages_rendered']} rendered) of {result['domain']}")
    print(f"  404s={a['status_404']} 5xx={a['status_5xx']} "
          f"dup_titles={a['dup_title']} missing_meta={a['missing_meta']} "
          f"thin={a['thin_pages']} orphans={a['orphan_pages']} "
          f"schema={a['schema_types_seen']}")
    print(f"  -> {args.out}")


if __name__ == "__main__":
    main()
