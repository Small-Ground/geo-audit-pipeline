# GEO Technical Audit — free-tier automation pipeline

Automates the *Beaumont Tiles – GEO Technical Audit* workbook with **no paid
tools**. Screaming Frog and the SEMrush API are intentionally excluded; a custom
crawler replaces Screaming Frog, and the off-page / competitive checks that only
SEMrush/Moz could answer are routed to manual review.

```
n8n (self-hosted)  →  crawler + PageSpeed Insights + GSC + GA4
                   →  LiteLLM → Gemini (Yes/No/N·A mapping + content judgments)
                   →  deterministic weighting (workbook math)
                   →  populated Excel + weighted score
```

## What's auto-scored vs manual

The question bank is extracted **straight from the Excel** (Excel stays the
source of truth). Of the 199 scored questions (≈1292 weight):

| | Weight | Source |
|---|---|---|
| **Auto (~86%)** | 1112 | custom crawler, PageSpeed Insights API, GSC API, GA4 API, LLM content judgments |
| **Manual (~14%)** | 180 | off-page (DA/backlinks), local listings (GBP/Bing/citations), AI-Overview citation testing |

That 86/14 split matches the original pipeline diagram's target — achieved on
free tiers only.

## Components

| Path | Role | Free? |
|---|---|---|
| `tools/extract_question_bank.py` | Excel → `config/question_bank.json` (questions, weights, source tags) | ✅ |
| `crawler/crawl.py` | Async crawler — the Screaming Frog replacement | ✅ |
| `collectors/psi.py` | PageSpeed Insights (Core Web Vitals, Lighthouse) | ✅ key |
| `collectors/google_apis.py` | GSC (sitemaps/index) + GA4 (events/goals) | ✅ service acct |
| `scorer/score.py` | LiteLLM→Gemini mapping + weighting + Excel writer | ✅ |
| `scorer/offline_rules.py` | Deterministic no-LLM fallback (demo) | ✅ |
| `docker-compose.yml` | n8n (self-hosted, Python baked in) + LiteLLM | ✅ |
| `n8n/geo_audit_workflow.json` | Importable orchestration workflow | ✅ |

## Scoring (reproduces the workbook exactly)

```
category numerator    = Σ weight where answer == "Yes"
category denominator  = Σ weight where answer in {"Yes","No"}   (N·A dropped)
category score        = numerator / denominator
total weighting score = Σ numerators / Σ denominators
```
Manual-review and not-yet-answered questions are excluded from the denominator
and reported as outstanding coverage. The output also reports **agreement with
the human-filled answers** still in the workbook — a live accuracy check.

## Quick start — local, no Docker, no API keys (offline demo)

```bash
pip install -r requirements.txt

# 1. Build the question bank from the workbook
python tools/extract_question_bank.py "<path>/Beaumont Tiles - GEO Technical Audit.xlsx" config/question_bank.json

# 2. Crawl (Screaming Frog replacement)
python crawler/crawl.py https://www.beaumont-tiles.com.au/ --max-pages 300 --out output/crawl.json

# 3. Score with deterministic rules only (no LLM) and write the Excel
python scorer/score.py --offline --out output/audit_populated.xlsx
```

## Full run (with free API keys + LLM)

```bash
cp .env.example .env        # fill GEMINI_API_KEY, PSI_API_KEY, GSC/GA4
# put the GSC/GA4 service-account JSON at ./secrets/service-account.json

docker compose up -d        # starts n8n (localhost:5678) + LiteLLM (localhost:4000)
# Import n8n/geo_audit_workflow.json in the n8n UI, set the domain, Run.
```
The workflow runs crawl → PSI → GSC/GA4 → score, writing
`output/audit_populated.xlsx` to this folder (shared volume).

## Notes & limitations
- **Scale:** the source audit crawled 54,079 URLs. For a prototype, cap the
  crawl (`--max-pages`). Full-site is slower but works; raise the cap and run
  the crawler with more time.
- **JS-rendered content:** the crawler reads server HTML. On heavily client-
  rendered sites (e.g. Magento PDPs), schema/OG may need a deeper crawl or a
  headless-render step (future: Playwright) to surface.
- **PSI quota:** unauthenticated PSI is rate-limited (429). Use a free
  `PSI_API_KEY`.
- **No Execute Command needed on Cloud:** since Screaming Frog is gone, every
  step is pure HTTP — the workflow can be rebuilt with HTTP Request nodes to run
  on n8n Cloud's free trial if you prefer not to self-host.
