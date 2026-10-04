"""
GEO Technical Audit — Streamlit UI for SEO analysts.

Enter a URL, click Run, watch progress, read the scorecard, download the Excel.
No terminal required. Launch with:  streamlit run ui/app.py   (or run_ui.ps1)
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"

ANSWER_COLORS = {
    "Yes": "#e4f3de", "No": "#faece7", "N/A": "#f1efe8",
    "MANUAL_REVIEW": "#faeeda", "PENDING_LLM": "#eeedfe",
}


def load_env() -> dict:
    """Keys from .env (local) merged with st.secrets (Streamlit Cloud).
    Secrets win, so the same app runs locally and deployed."""
    env, p = {}, ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                v = m.group(2).strip().strip('"').strip("'")
                if v:
                    env[m.group(1)] = v
    try:  # st.secrets raises if no secrets configured — fine locally
        env.update({k: str(v) for k, v in st.secrets.items()})
    except Exception:  # noqa: BLE001
        pass
    return env


def run_step(label, args, env, status):
    status.update(label=label)
    r = subprocess.run([sys.executable] + args, cwd=ROOT, env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        st.error(f"**{label}** failed:\n\n```\n{(r.stderr or r.stdout)[-1500:]}\n```")
        st.stop()
    return r


# --------------------------------------------------------------------------- #
st.set_page_config(page_title="GEO Technical Audit", page_icon="📊", layout="wide")
st.title("📊 GEO Technical Audit")
st.caption("Free-tier automated audit — enter a domain, get a populated, weighted scorecard.")

env_vals = load_env()
proc_env = {**os.environ, **env_vals}
proc_env.setdefault("LITELLM_BASE_URL",
                    env_vals.get("LITELLM_BASE_URL", "https://api.groq.com/openai/v1"))
proc_env["LLM_MODEL"] = env_vals.get("LLM_MODEL", "llama-3.3-70b-versatile")
if env_vals.get("GROQ_API_KEY") and not proc_env.get("LITELLM_API_KEY"):
    proc_env["LITELLM_API_KEY"] = env_vals["GROQ_API_KEY"]

with st.sidebar:
    st.header("Configuration")
    groq_ok = bool(env_vals.get("GROQ_API_KEY"))
    psi_ok = bool(env_vals.get("PSI_API_KEY"))
    gsc_ok = bool(env_vals.get("GSC_SITE_URL")) and (ROOT / "secrets/service-account.json").exists()
    st.markdown(f"- LLM (Groq): {'✅ ready' if groq_ok else '❌ missing key'}")
    st.markdown(f"- PageSpeed Insights: {'✅ ready' if psi_ok else '⚠️ not set'}")
    st.markdown(f"- Search Console / GA4: {'✅ ready' if gsc_ok else '⚪ optional'}")
    st.divider()
    max_pages = st.slider("Pages to crawl", 20, 1000, 150, step=10,
                          help="Higher = more thorough but slower. 54k-page sites: sample first.")
    offline = st.checkbox("Offline mode (no LLM)", value=not groq_ok,
                          help="Deterministic rules only — no API key needed, lower coverage.")

domain = st.text_input("Website URL", placeholder="https://www.example.com/")
go = st.button("▶  Run audit", type="primary", disabled=not domain.strip())
st.caption("On hosted free environments keep pages modest (≤150) to avoid timeouts. "
           "For a full audit, run it locally and upload the results.json below.")

if go:
    url = domain.strip()
    if not url.startswith("http"):
        url = "https://" + url
    with st.status("Running audit…", expanded=True) as status:
        st.write("🕷️  Crawling site (Screaming Frog replacement)…")
        run_step("Crawling…", ["crawler/crawl.py", url, "--max-pages", str(max_pages),
                               "--out", "output/crawl.json"], proc_env, status)
        if psi_ok:
            st.write("⚡  PageSpeed Insights…")
            run_step("PageSpeed Insights…", ["collectors/psi.py", url,
                     "--out", "output/psi.json"], proc_env, status)
        st.write("🔍  Search Console + GA4…")
        run_step("GSC + GA4…", ["collectors/google_apis.py", "--out-gsc",
                 "output/gsc.json", "--out-ga4", "output/ga4.json"], proc_env, status)
        st.write("🤖  Scoring with LLM + weighting…" if not offline
                 else "📐  Scoring (offline rules)…")
        score_args = ["scorer/score.py", "--out", "output/audit_populated.xlsx"]
        if offline:
            score_args.append("--offline")
        run_step("Scoring…", score_args, proc_env, status)
        status.update(label="Audit complete ✅", state="complete", expanded=False)

# Viewer mode — display an audit produced elsewhere (e.g. a heavy local run),
# without running the crawl in this (possibly resource-limited) environment.
with st.expander("📂 Or view an existing audit — upload a results.json"):
    up = st.file_uploader("results.json", type="json", label_visibility="collapsed")
    if up is not None:
        OUTPUT.mkdir(exist_ok=True)
        (OUTPUT / "results.json").write_bytes(up.getvalue())
        st.success("Loaded — scorecard below.")

# --------------------------------------------------------------------------- #
# Results
res_path = OUTPUT / "results.json"
if not res_path.exists():
    st.info("No audit yet — enter a URL above and click **Run audit**.")
    st.stop()

data = json.loads(res_path.read_text(encoding="utf-8"))
meta, summary, results = data["meta"], data["summary"], data["results"]
total, cats = summary["total"], summary["categories"]

st.divider()
st.subheader(f"Results — {meta.get('domain', '')}")
st.caption(f"Generated {meta.get('generated', '')[:16].replace('T', ' ')} · "
           f"{meta.get('pages_crawled', 0)} pages crawled")

c1, c2, c3, c4 = st.columns(4)
score = total.get("score")
c1.metric("Total Weighting Score", f"{score * 100:.1f}%" if score is not None else "n/a")
auto = sum(1 for r in results if r["answer"] in ("Yes", "No"))
manual = sum(1 for r in results if r["answer"] == "MANUAL_REVIEW")
c2.metric("Auto-answered", auto)
c3.metric("Manual review", manual)
if total.get("ref_total"):
    pct = total["ref_match"] / total["ref_total"] * 100
    c4.metric("Agreement w/ human", f"{pct:.0f}%", help=f"{total['ref_match']}/{total['ref_total']} matched")

xlsx = OUTPUT / "audit_populated.xlsx"
if xlsx.exists():
    safe = re.sub(r"[^a-z0-9]+", "-", (meta.get("domain") or "site").lower()).strip("-")
    st.download_button("⬇  Download full Excel audit", xlsx.read_bytes(),
                       file_name=f"GEO-Audit-{safe}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary")

# Category scorecard
rows = []
for name, c in cats.items():
    rows.append({"Category": name,
                 "Score %": round(c["score"] * 100, 1) if c["score"] is not None else None,
                 "Earned": round(c["num"]), "Scored wt": round(c["den"]),
                 "Manual wt": round(c["manual_w"]), "Pending wt": round(c["pending_w"])})
df = pd.DataFrame(rows).sort_values("Category")

st.subheader("Category scores")
score_df = df[df["Score %"].notna()].copy()
_chart = (
    alt.Chart(score_df)
    .mark_bar()
    .encode(
        x=alt.X("Score %:Q", scale=alt.Scale(domain=[0, 100]), title="Score %"),
        y=alt.Y("Category:N", sort="-x", title=None),
        color=alt.value("#534ab7"),
        tooltip=["Category", "Score %"],
    )
    .properties(height=int(score_df["Category"].nunique() * 28) + 40)
)
st.altair_chart(_chart, use_container_width=True)
st.dataframe(df, hide_index=True, use_container_width=True, column_config={
    "Score %":    st.column_config.NumberColumn(width="small"),
    "Earned":     st.column_config.NumberColumn(width="small"),
    "Scored wt":  st.column_config.NumberColumn(width="small"),
    "Manual wt":  st.column_config.NumberColumn(width="small"),
    "Pending wt": st.column_config.NumberColumn(width="small"),
})

# Per-question detail
st.subheader("Question detail")
by_cat = {}
for r in results:
    by_cat.setdefault(r["category"], []).append(r)

for name in sorted(by_cat):
    c = cats[name]
    sc = f"{c['score'] * 100:.0f}%" if c["score"] is not None else "n/a"
    with st.expander(f"**{name}** — {sc}  ({len(by_cat[name])} questions)"):
        qdf = pd.DataFrame([{
            "Question": r["question"], "Answer": r["answer"], "Source": r["source"],
            "Weight": r["weight"], "Why": r["rationale"],
            "Human ref": r.get("reference_answer", ""),
        } for r in by_cat[name]])
        styled = qdf.style.map(
            lambda v: f"background-color:{ANSWER_COLORS.get(v, '')}", subset=["Answer"])
        st.dataframe(styled, hide_index=True, use_container_width=True)
