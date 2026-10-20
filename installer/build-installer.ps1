param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [string]$Output = (Join-Path (Split-Path -Parent $PSScriptRoot) "..\..\outputs\VidoSwift-Setup.exe")
)
$ErrorActionPreference = "Stop"
$installerRoot = $PSScriptRoot
$root = Split-Path -Parent $installerRoot
$outputPath = [IO.Path]::GetFullPath($Output)
$buildKey = "{0}-{1}" -f [IO.Path]::GetFileNameWithoutExtension($outputPath), (Get-Date -Format "yyyyMMddHHmmssfff")
$buildWork = Join-Path $root "work\installer-build-$buildKey"
$stage = Join-Path $buildWork "stage"
$stub = Join-Path $buildWork "VidoSwift-Setup-stub.exe"
$payload = Join-Path $buildWork "VidoSwift-Setup-payload.zip"
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { throw "Application executable not found: $Executable" }
$appSource = Split-Path -Parent ([IO.Path]::GetFullPath($Executable))
if (-not (Test-Path -LiteralPath (Join-Path $appSource "_internal") -PathType Container)) {
    throw "Expected an onedir application folder containing _internal: $appSource"
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Get-ChildItem -LiteralPath $appSource -Force | Copy-Item -Destination $stage -Recurse -Force
Copy-Item -LiteralPath (Join-Path $installerRoot "install.ps1") -Destination $stage
Copy-Item -LiteralPath (Join-Path $installerRoot "uninstall.ps1") -Destination $stage
Copy-Item -LiteralPath (Join-Path $root "THIRD-PARTY-NOTICES.md") -Destination $stage
New-Item -ItemType Directory -Path (Split-Path -Parent $outputPath) -Force | Out-Null

$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $compiler)) { throw ".NET Framework C# compiler was not found; cannot build the self-extracting installer." }
$setupIcon = Join-Path $root "backend\VidoSwift.ico"
$compilerArguments = @("/nologo", "/target:winexe", "/out:$stub", "/reference:System.Drawing.dll", "/reference:System.IO.Compression.dll", "/reference:System.IO.Compression.FileSystem.dll", "/reference:System.Windows.Forms.dll")
if (Test-Path -LiteralPath $setupIcon -PathType Leaf) { $compilerArguments += "/win32icon:$setupIcon" }
$compilerArguments += (Join-Path $installerRoot "setup-stub.cs")
& $compiler @compilerArguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $stub -PathType Leaf)) { throw "Could not compile the self-extracting installer stub." }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $payload -CompressionLevel Optimal

$archive = [IO.File]::OpenRead($payload)
$package = [IO.File]::Open($stub, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::Read)
try {
    $package.Position = $package.Length
    $archive.CopyTo($package)
    $package.Write([byte[]](0x56, 0x53, 0x57, 0x46), 0, 4)
    $length = [BitConverter]::GetBytes([long]$archive.Length)
    $package.Write($length, 0, $length.Length)
} finally { $archive.Dispose(); $package.Dispose() }
Copy-Item -LiteralPath $stub -Destination $outputPath -Force
$item = Get-Item -LiteralPath $outputPath
if ($item.Length -lt [Math]::Max(1MB, (Get-Item -LiteralPath $Executable).Length * 0.9)) { throw "Installer is unexpectedly small ($($item.Length) bytes)." }
Write-Output "Installer created: $outputPath ($($item.Length) bytes)"
