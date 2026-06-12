#!/usr/bin/env bash
# One-command GEO audit runner (no n8n / no Docker) — macOS/Linux collaborators.
#   ./run_audit.sh https://www.example.com/
#   ./run_audit.sh https://www.example.com/ 500       # max pages
#   OFFLINE=1 ./run_audit.sh https://www.example.com/ # deterministic, no LLM
set -euo pipefail

DOMAIN="${1:?usage: ./run_audit.sh <domain> [max_pages]}"
MAX_PAGES="${2:-300}"
cd "$(dirname "$0")"

# load .env
if [ -f .env ]; then set -a; . ./.env; set +a; fi
export LITELLM_BASE_URL="${LITELLM_BASE_URL:-https://api.groq.com/openai/v1}"
export LLM_MODEL="${LLM_MODEL:-llama-3.3-70b-versatile}"
export LITELLM_API_KEY="${LITELLM_API_KEY:-${GROQ_API_KEY:-}}"

echo "=== GEO Technical Audit: $DOMAIN ==="

echo "[1/4] Crawling (Screaming Frog replacement)..."
python3 crawler/crawl.py "$DOMAIN" --max-pages "$MAX_PAGES" --out output/crawl.json

echo "[2/4] PageSpeed Insights..."
if [ -n "${PSI_API_KEY:-}" ]; then python3 collectors/psi.py "$DOMAIN" --out output/psi.json
else echo "  (skipped - PSI_API_KEY not set)"; fi

echo "[3/4] Search Console + GA4..."
python3 collectors/google_apis.py --out-gsc output/gsc.json --out-ga4 output/ga4.json

echo "[4/4] Scoring + writing Excel..."
if [ "${OFFLINE:-0}" = "1" ]; then python3 scorer/score.py --offline --out output/audit_populated.xlsx
else python3 scorer/score.py --out output/audit_populated.xlsx; fi

echo "Done -> output/audit_populated.xlsx"
