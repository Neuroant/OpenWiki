#!/usr/bin/env pwsh
# Deploy the `openwiki` CLI as a GLOBAL command via pipx, in an isolated Python 3.13
# venv (Kuzu has no 3.14 wheel). Run once; then `openwiki` works from any folder and
# you create projects with `openwiki init` (no repo clone / venv per project).
#
#   .\install-openwiki.ps1              # install from THIS checkout
#   .\install-openwiki.ps1 -Git         # install from GitHub instead
#   .\install-openwiki.ps1 -Editable    # editable install of this checkout (for hacking)
param([switch]$Git, [switch]$Editable)
$ErrorActionPreference = 'Stop'

# 1. Locate Python 3.13.
try { $py = (& py -3.13 -c "import sys; print(sys.executable)").Trim() }
catch {
    Write-Error "Python 3.13 not found. Install it from https://www.python.org/downloads/ -- 3.14 will NOT work (no Kuzu wheel)."
    exit 1
}
Write-Host "Using Python 3.13: $py"

# 2. Ensure pipx is available for that interpreter.
& $py -m pipx --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing pipx..."
    & $py -m pip install --user --upgrade pipx
}

# 3. Pick the source (local checkout by default; GitHub with -Git).
if ($Git) { $source = "git+https://github.com/Neuroant/OpenWiki.git" }
else      { $source = $PSScriptRoot }
$extra = @(); if ($Editable -and -not $Git) { $extra = @('--editable') }

# 4. Install (into pipx's own 3.13 venv; --force reinstalls if present).
Write-Host "Installing openwiki from $source ..."
& $py -m pipx install --force --python $py @extra $source

# 5. Make sure pipx's bin dir is on PATH.
& $py -m pipx ensurepath | Out-Null

Write-Host ""
Write-Host "Done. Open a NEW terminal, then verify:  openwiki --help"
Write-Host "Create a project anywhere:"
Write-Host "  openwiki init my-wiki --source path\to\doc.pdf"
Write-Host "  cd my-wiki; openwiki build; openwiki serve --port 8137"
