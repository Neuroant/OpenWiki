#!/usr/bin/env pwsh
# Launch the local, wiki-aware OpenCode "openwiki" agent from the repo root.
# Any extra args are passed through to opencode, e.g.:  .\start-opencode.ps1 run "Was ist SST?"
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    Write-Error 'opencode not found on PATH. Install it: https://opencode.ai/docs/'
    exit 1
}
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Error '.venv missing. Create it:  py -m venv .venv;  .venv\Scripts\python -m pip install -e ".[dev]"'
    exit 1
}
if (-not (Test-Path 'output\graph')) {
    Write-Warning 'output\graph missing - the graph tools (neighbors/find_path/find_entity) will be unavailable. Build with: openwiki graph-build ...'
}
try { Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -TimeoutSec 2 | Out-Null }
catch { Write-Warning 'Ollama not reachable at http://localhost:11434 - start it (ollama serve) and pull qwen3:30b-a3b-instruct-2507-q4_K_M + bge-m3.' }

opencode @args
