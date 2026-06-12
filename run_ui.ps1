#!/usr/bin/env pwsh
# Launch the GEO Audit web UI for SEO analysts.
#   .\run_ui.ps1
# Opens http://localhost:8501 in the browser.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m streamlit run ui/app.py
