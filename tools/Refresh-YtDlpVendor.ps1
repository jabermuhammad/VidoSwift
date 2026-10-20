<#
.SYNOPSIS
  Replace backend\vendor with a complete, dependency-consistent copy of the yt-dlp pinned in
  backend\requirements.txt (default: 2026.10.10.233141.dev0, the first build containing the
  upstream fix for "The page needs to be reloaded", yt-dlp #17389 / PRs #17723, #17805).

.DESCRIPTION
  Installs the pinned requirement (with its [default] extras: yt-dlp-ejs, websockets, requests,
  certifi, pycryptodomex, brotli, mutagen, ...) into a fresh folder with pip, checks the version,
  then swaps it in. The old vendor folder is kept as backend\vendor.backup-<timestamp>.
  Replacing only extractor\youtube\_base.py/_video.py would be an incomplete refresh: the
  2026.08.19 -> 2026.10.10 change touches 32 files (YoutubeDL.py, utils, networking, ...).
  Run build.ps1 afterwards: it runs the tests and rebuilds the EXE.
#>
param([string]$Python = $env:VIDOSWIFT_PYTHON)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) { throw "Set VIDOSWIFT_PYTHON (or pass -Python) to a Python 3.10+ executable." }
$pin = (Get-Content -LiteralPath (Join-Path $backend "requirements.txt") | Where-Object { $_ -match '^\s*yt-dlp\[' } | Select-Object -First 1).Trim()
if (-not $pin) { throw "No yt-dlp requirement found in backend\requirements.txt." }
$expected = ([regex]::Match($pin, '==\s*([^\s;]+)')).Groups[1].Value
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$fresh = Join-Path $backend "vendor.new-$stamp"
$vendor = Join-Path $backend "vendor"
& $Python -m pip install --disable-pip-version-check --target $fresh $pin
if ($LASTEXITCODE -ne 0) { throw "pip could not install $pin." }
$installed = & $Python -c "import sys; sys.path.insert(0, r'$fresh'); import yt_dlp.version as v; print(v.__version__.split('.dev')[0])"
if ($LASTEXITCODE -ne 0 -or -not $installed) { throw "The refreshed vendor folder does not import yt_dlp." }
if ($expected -and -not ($expected -like "$($installed.Trim())*")) { throw "Installed yt-dlp $installed does not match the pin $expected." }
if (Test-Path -LiteralPath $vendor) { Move-Item -LiteralPath $vendor -Destination (Join-Path $backend "vendor.backup-$stamp") }
Move-Item -LiteralPath $fresh -Destination $vendor
Write-Output "Vendored yt-dlp $installed from '$pin'. Previous vendor kept as backend\vendor.backup-$stamp. Now run .\build.ps1"
