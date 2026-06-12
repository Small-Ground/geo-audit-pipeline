# Deploying the UI to Streamlit Community Cloud (free)

Gives SEO analysts a shareable URL — no install, no terminal. Free tier.

## Before you start
- The repo is already deploy-ready: `ui/app.py` (entry point), `requirements.txt`
  at the root (includes Streamlit), `.streamlit/config.toml`, and the app reads
  keys from **Streamlit secrets** when deployed.
- You'll need your **Groq API key** (and optionally a PSI key).

## Steps
1. Go to **https://share.streamlit.io** and sign in **with GitHub** (the
   `fredman08` account that owns the repo).
2. Click **Create app → Deploy a public app from GitHub** (private repos are
   supported on the free tier once you grant access).
3. Fill in:
   - **Repository:** `fredman08/geo-audit-pipeline`
   - **Branch:** `main`
   - **Main file path:** `ui/app.py`
4. Open **Advanced settings → Secrets** and paste (from
   `.streamlit/secrets.toml.example`):
   ```toml
   GROQ_API_KEY = "gsk_your_key"
   LITELLM_API_KEY = "gsk_your_key"
   LLM_MODEL = "llama-3.3-70b-versatile"
   LITELLM_BASE_URL = "https://api.groq.com/openai/v1"
   PSI_API_KEY = ""
   ```
5. Click **Deploy**. First build takes a few minutes (installing deps).
6. You get a URL like `https://geo-audit-pipeline.streamlit.app` — share it, or
   invite viewers via the app's **Settings → Sharing**.

## How analysts use it
- **Live audit:** paste a URL, keep pages modest (**≤150** — the free sandbox is
  ~1 GB RAM / CPU-throttled, big crawls can time out), click Run.
- **View a full audit:** for large sites, run locally
  (`.\run_audit.ps1 <url> -MaxPages 500`, optionally `--render`), then in the
  cloud app use **"Or view an existing audit — upload a results.json"** to
  explore/share the full result without re-crawling in the sandbox.

## Notes
- **Confidentiality:** deploying runs crawls on Streamlit/Snowflake infrastructure
  and grants Streamlit OAuth access to the repo. For client-confidential work,
  prefer running locally or on the organisation-internal infrastructure.
- **Playwright `--render`** is **not** available on the cloud sandbox (browser
  binaries are too heavy) — use it locally and upload results.
- **Sleeping:** free apps sleep after inactivity and wake on next visit.
