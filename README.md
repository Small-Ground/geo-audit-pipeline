# GEO Technical Audit — free-tier automation pipeline

Automates a *GEO Technical Audit* workbook with **no paid
tools**. Screaming Frog and the SEMrush API are intentionally excluded; a custom
crawler replaces Screaming Frog, and the off-page / competitive checks that only
SEMrush/Moz could answer are routed to manual review.

```
Streamlit UI  →  crawler + PageSpeed Insights + GSC + GA4
              →  Groq llama-3.3-70b (Yes/No/N·A mapping + content judgments)
              →  deterministic weighting (workbook math)
              →  populated Excel + weighted score + scorecard
```

📐 **[Architecture diagram & old→new mapping](docs/ARCHITECTURE.md)** — how this
mirrors the original n8n flow on free tiers.

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
| `scorer/score.py` | Groq llama-3.3-70b mapping + weighting + Excel writer | ✅ |
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

## For SEO analysts — the web UI (no terminal)

```powershell
pip install -r requirements.txt -r ui/requirements.txt
.\run_ui.ps1            # opens http://localhost:8501
```
Paste a URL → **Run audit** → watch progress → read the scorecard (total score,
per-category bars, every question with its answer/source/rationale colour-coded)
→ **Download full Excel audit**. Keys are read from `.env`; the sidebar shows
which data sources are configured and has a crawl-depth slider + offline toggle.

> Deploy free to Streamlit Community Cloud for a shareable link — see
> **[docs/DEPLOY.md](docs/DEPLOY.md)**. The app reads keys from Streamlit secrets
> when hosted, and supports uploading a `results.json` to view a full audit run
> locally (keeping heavy crawls out of the cloud sandbox).

## Run it — one command, no n8n, no Docker

```powershell
pip install -r requirements.txt
cp .env.example .env        # add your GROQ_API_KEY (+ PSI_API_KEY)

# Build the question bank once from the workbook
python tools/extract_question_bank.py "<path>/GEO Technical Audit.xlsx" config/question_bank.json

# Run the whole pipeline (crawl -> PSI -> GSC/GA4 -> score -> Excel)
.\run_audit.ps1 https://www.example.com/
```
macOS/Linux: `./run_audit.sh https://www.example.com/`
Add `-Offline` (PowerShell) / `OFFLINE=1` (bash) to score with deterministic
rules only — no LLM key needed.

The runner reads keys from `.env`, points the scorer straight at Groq
(OpenAI-compatible), and writes `output/audit_populated.xlsx`.

### Schedule it (optional, replaces n8n's schedule trigger)
No n8n needed — use Windows Task Scheduler:
```powershell
$action  = New-ScheduledTaskAction -Execute "pwsh" -Argument "-File `"$PWD\run_audit.ps1`" https://www.example.com/"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 6am
Register-ScheduledTask -TaskName "GEO-Audit-Weekly" -Action $action -Trigger $trigger
```
(cron equivalent on macOS/Linux: `0 6 * * 1 /path/run_audit.sh https://...`)

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
- **Crawl coverage:** the crawler seeds from the sitemap (`--sitemap-seed N`,
  default 60) so PDPs/categories/blog pages are actually visited, not just what's
  linked from the homepage. This is the main accuracy lever.
- **JS-rendered content:** the crawler reads server HTML (sufficient for most
  sites — schema is usually server-rendered). For genuinely client-rendered
  sites, add `--render` to re-read pages through headless Chromium
  (`pip install -r crawler/requirements-render.txt && python -m playwright install chromium`).
- **PSI quota:** unauthenticated PSI is rate-limited (429). Use a free
  `PSI_API_KEY`.
- **No Execute Command needed on Cloud:** since Screaming Frog is gone, every
  step is pure HTTP — the workflow can be rebuilt with HTTP Request nodes to run
  on n8n Cloud's free trial if you prefer not to self-host.
