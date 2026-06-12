# Architecture

This pipeline mirrors the original GEO-audit flow, re-built on **100% free
tiers** — Screaming Frog and the SEMrush API are removed, n8n is optional, and
the LLM runs on Groq's free tier. SEO analysts drive it from a web UI.

![Architecture diagram](architecture.svg)

## Flow (renders on GitHub)

```mermaid
flowchart TD
    T["Trigger — domain input<br/><i>Streamlit UI / CLI / Task Scheduler</i>"]:::trigger
    O["Pipeline orchestrator<br/><i>run_audit.ps1 — local Python (n8n optional)</i>"]:::proc

    T --> O

    O --> CR["Custom crawler<br/><i>replaces Screaming Frog</i>"]:::data
    O --> PSI["PageSpeed Insights API<br/><i>Core Web Vitals, free</i>"]:::data
    O --> GSC["Search Console API<br/><i>sitemaps, indexing · optional</i>"]:::data
    O --> GA4["GA4 Data API<br/><i>events, goals · optional</i>"]:::data

    CR --> M["Merge + parse evidence<br/><i>score.py</i>"]:::proc
    PSI --> M
    GSC --> M
    GA4 --> M

    M --> L["LLM scoring — Groq<br/><i>llama-3.3-70b · maps data → Yes/No/N·A + weighted score</i>"]:::proc
    L --> R{"Route by source mode"}:::router
    R -->|~86%| A["Auto-scored<br/><i>crawler · PSI · GSC · GA4 · LLM</i>"]:::auto
    R -->|~14%| MAN["Manual review<br/><i>DA/backlinks · GBP · AIO citation</i>"]:::manual
    A --> OUT["Populated audit output<br/><i>Excel workbook + results.json</i>"]:::output
    MAN --> OUT
    OUT --> UI["Streamlit scorecard<br/><i>score · per-question detail · download</i>"]:::trigger

    classDef trigger fill:#f1efe8,stroke:#5f5e5a;
    classDef proc fill:#eeedfe,stroke:#534ab7;
    classDef data fill:#e1f5ee,stroke:#0f6e56;
    classDef router fill:#faeeda,stroke:#85540b;
    classDef auto fill:#eaf3de,stroke:#3b6d11;
    classDef manual fill:#faece7,stroke:#993c1d;
    classDef output fill:#e6f1fb,stroke:#185fa5;
```

## What changed vs the original n8n flow

| Original (n8n diagram) | This prototype | Why |
|---|---|---|
| n8n schedule / webhook trigger | **Streamlit UI** (or CLI / Task Scheduler) | Analyst-friendly, no n8n account |
| n8n orchestrator (Execute Command) | **`run_audit.ps1` — local Python** | No Docker/Execute Command needed once Screaming Frog is gone |
| Screaming Frog CLI (Docker sidecar) | **Custom crawler** (`crawler/crawl.py`) | Paid licence removed; free async crawler |
| SEMrush API | **dropped** → manual-review branch | No free tier |
| GSC / GA4 APIs | **unchanged** (free) | Still free |
| — | **PageSpeed Insights API** (new) | Free Core Web Vitals — wasn't in the original |
| n8n Merge + Code node | **`score.py` evidence assembly** | Same role, in code |
| LiteLLM → Gemini | **Groq `llama-3.3-70b`** (OpenAI-compatible, LiteLLM-ready) | Gemini free tier blocked on Workspace accounts |
| n8n IF node router | **`mode` tag** in the question bank | Auto vs manual split, deterministic |
| Excel template output | **Excel + `results.json`** | Adds machine-readable output for the UI |

## Auto vs manual split

Auto-scored ≈ **86%** of weight; manual ≈ **14%**. The manual residue is exactly
the off-page / competitive / local checks that only paid tools (SEMrush/Moz) or
human judgement can answer: domain authority & backlinks, Google Business Profile
/ local listings, and AI-Overview citation testing.
