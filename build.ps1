$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ui = Join-Path $root "ui"
$backend = Join-Path $root "backend"
$nodeRuntime = Join-Path $backend "bin\node.exe"
if (-not (Test-Path -LiteralPath $nodeRuntime -PathType Leaf)) {
    $nodeSource = $env:VIDOSWIFT_NODE
    if (-not $nodeSource) {
        $nodeCommand = Get-Command node -ErrorAction SilentlyContinue
        if ($nodeCommand) { $nodeSource = $nodeCommand.Source }
    }
    if ($nodeSource -and (Test-Path -LiteralPath $nodeSource -PathType Leaf)) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $nodeRuntime) -Force | Out-Null
        Copy-Item -LiteralPath $nodeSource -Destination $nodeRuntime
    }
}
if (-not (Test-Path -LiteralPath $nodeRuntime -PathType Leaf)) {
    throw "Node.js 22+ is required for YouTube JavaScript challenge support. Install Node.js or set VIDOSWIFT_NODE to node.exe before building."
}
$nodeVersion = (& $nodeRuntime --version 2>$null).Trim()
$nodeExitCode = $LASTEXITCODE
$nodeVersionMatch = [regex]::Match($nodeVersion, '^v(\d+)\.')
if ($nodeExitCode -ne 0 -or -not $nodeVersionMatch.Success -or [int]$nodeVersionMatch.Groups[1].Value -lt 22) {
    throw "A working Node.js 22+ runtime is required; found '$nodeVersion'."
}
Write-Output "Bundling YouTube JavaScript runtime: $nodeVersion"
$nodeLicense = Join-Path $backend "bin\node-LICENSE.txt"
if (-not (Test-Path -LiteralPath $nodeLicense -PathType Leaf)) {
    throw "The Node.js license bundle is missing: $nodeLicense. Keep the upstream LICENSE file beside node.exe before packaging."
}
$releasePath = $env:VIDOSWIFT_DISTPATH
if (-not $releasePath) { $releasePath = Join-Path $root "release" }
$pyinstallerWorkPath = $env:VIDOSWIFT_PYINSTALLER_WORKPATH
if (-not $pyinstallerWorkPath) { $pyinstallerWorkPath = Join-Path $root "work\pyinstaller" }
New-Item -ItemType Directory -Path $releasePath,$pyinstallerWorkPath -Force | Out-Null

Push-Location $ui
try {
    if (-not (Test-Path (Join-Path $ui "node_modules"))) { pnpm install --frozen-lockfile }
    pnpm build
    if ($LASTEXITCODE -ne 0) { throw "Vite build failed." }
    pnpm exec tsc --noEmit
    if ($LASTEXITCODE -ne 0) { throw "TypeScript check failed." }
} finally { Pop-Location }

$python = $env:VIDOSWIFT_PYTHON
if (-not $python) {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & $pyLauncher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null | ForEach-Object { $python = $_ }
    }
}
if (-not $python -or -not (Test-Path $python)) {
    throw "Set VIDOSWIFT_PYTHON to a Python 3.10+ executable (or install the Python launcher)."
}

$env:PYTHONPATH = (Join-Path $backend "vendor") + [IO.Path]::PathSeparator + $env:PYTHONPATH
$ffmpegPath = & $python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $ffmpegPath -PathType Leaf)) {
    throw "The bundled FFmpeg binary from imageio-ffmpeg could not be found."
}
$binDir = Join-Path $backend "bin"
New-Item -ItemType Directory -Path $binDir -Force | Out-Null
Copy-Item -LiteralPath $ffmpegPath -Destination (Join-Path $binDir "ffmpeg.exe") -Force
Push-Location $backend
try {
    $testRunner = "import sys,unittest; sys.argv[0]='app.py'; suite=unittest.defaultTestLoader.discover('tests'); result=unittest.TextTestRunner(verbosity=2).run(suite); raise SystemExit(not result.wasSuccessful())"
    & $python -c $testRunner
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
    $sourceText = Get-Content -LiteralPath (Join-Path $backend "app.py") -Raw
    $buildMatch = [regex]::Match($sourceText, 'APP_BUILD_VERSION\s*=\s*"([^"]+)"')
    if (-not $buildMatch.Success) { throw "APP_BUILD_VERSION is missing from backend/app.py." }
    $ytDlpVersion = & $python -c "from yt_dlp.version import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) { throw "Could not read the bundled yt-dlp version." }
    $ytDlpVersionParts = [regex]::Match($ytDlpVersion.Trim(), '^(\d{4})\.(\d+)\.(\d+)')
    if ($ytDlpVersionParts.Success -and [version]::new([int]$ytDlpVersionParts.Groups[1].Value, [int]$ytDlpVersionParts.Groups[2].Value, [int]$ytDlpVersionParts.Groups[3].Value) -lt [version]::new(2026, 10, 10)) {
        Write-Warning "Bundled yt-dlp $($ytDlpVersion.Trim()) predates YouTube's tv_downgraded fix. VidoSwift applies a compatibility workaround; run tools\Refresh-YtDlpVendor.ps1 to bundle the pinned upstream build."
    }
    $metadataDir = Join-Path $root "work\package-metadata"
    New-Item -ItemType Directory -Path $metadataDir -Force | Out-Null
    $buildInfoPath = Join-Path $metadataDir ("vidoswift-build-info-{0}.json" -f [guid]::NewGuid().ToString("N"))
    $buildInfo = [ordered]@{
        app_build_version = $buildMatch.Groups[1].Value
        backend_app_sha256 = (Get-FileHash -LiteralPath (Join-Path $backend "app.py") -Algorithm SHA256).Hash.ToLowerInvariant()
        yt_dlp_version = $ytDlpVersion.Trim()
        node_js_version = $nodeVersion
        node_js_sha256 = (Get-FileHash -LiteralPath $nodeRuntime -Algorithm SHA256).Hash.ToLowerInvariant()
        ui_index_sha256 = (Get-FileHash -LiteralPath (Join-Path $ui "dist\index.html") -Algorithm SHA256).Hash.ToLowerInvariant()
        built_utc = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    }
    $buildInfo | ConvertTo-Json | Set-Content -LiteralPath $buildInfoPath -Encoding UTF8
    $env:VIDOSWIFT_BUILD_INFO_PATH = $buildInfoPath
    & $python -m PyInstaller --noconfirm --clean --distpath $releasePath --workpath $pyinstallerWorkPath VidoSwift.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    Copy-Item -LiteralPath $buildInfoPath -Destination (Join-Path $releasePath "VidoSwift\vidoswift-build-info.json")
} finally { Pop-Location }

Write-Output "Built: $(Join-Path $releasePath 'VidoSwift\VidoSwift.exe')"
