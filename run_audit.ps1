#!/usr/bin/env pwsh
# One-command GEO audit runner (no n8n / no Docker).
#   .\run_audit.ps1 https://www.example.com/
#   .\run_audit.ps1 https://www.example.com/ -MaxPages 500
#   .\run_audit.ps1 https://www.example.com/ -Offline      # deterministic, no LLM
#
# Reads keys from .env, runs crawl -> PSI -> GSC/GA4 -> score, writes the Excel.
param(
    [Parameter(Mandatory = $true, Position = 0)] [string]$Domain,
    [int]$MaxPages = 300,
    [int]$ImgCap = 300,
    [switch]$Offline,
    [string]$Out = "output/audit_populated.xlsx"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --- load .env into the process environment ---
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $val = $Matches[2].Trim().Trim('"').Trim("'")
            if ($val) { Set-Item -Path "env:$($Matches[1])" -Value $val }
        }
    }
}

# --- local LLM routing: scorer talks straight to Groq (OpenAI-compatible) ---
if (-not $env:LITELLM_BASE_URL) { $env:LITELLM_BASE_URL = "https://api.groq.com/openai/v1" }
if (-not $env:LLM_MODEL) { $env:LLM_MODEL = "llama-3.3-70b-versatile" }
if (-not $env:LITELLM_API_KEY -and $env:GROQ_API_KEY) { $env:LITELLM_API_KEY = $env:GROQ_API_KEY }

Write-Host "=== GEO Technical Audit: $Domain ===" -ForegroundColor Cyan

Write-Host "`n[1/4] Crawling (Screaming Frog replacement)..." -ForegroundColor Yellow
python crawler/crawl.py $Domain --max-pages $MaxPages --img-cap $ImgCap --out output/crawl.json

Write-Host "`n[2/4] PageSpeed Insights..." -ForegroundColor Yellow
if ($env:PSI_API_KEY) { python collectors/psi.py $Domain --out output/psi.json }
else { Write-Host "  (skipped - PSI_API_KEY not set)" -ForegroundColor DarkGray }

Write-Host "`n[3/4] Search Console + GA4..." -ForegroundColor Yellow
python collectors/google_apis.py --out-gsc output/gsc.json --out-ga4 output/ga4.json

Write-Host "`n[4/4] Scoring + writing Excel..." -ForegroundColor Yellow
if ($Offline) { python scorer/score.py --offline --out $Out }
else { python scorer/score.py --out $Out }

Write-Host "`nDone -> $Out" -ForegroundColor Green
