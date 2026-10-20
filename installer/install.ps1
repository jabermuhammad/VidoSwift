param(
    [string]$InstallPath,
    [string]$ProgressFile,
    [switch]$Quiet
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms

function Resolve-VidoSwiftInstallTarget([string]$SelectedPath) {
    $path = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($SelectedPath))
    $pathRoot = [IO.Path]::GetPathRoot($path)
    if (-not [string]::Equals($path, $pathRoot, [StringComparison]::OrdinalIgnoreCase)) {
        $path = $path.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
    }
    if ([string]::Equals((Split-Path -Leaf $path), "VidoSwift", [StringComparison]::OrdinalIgnoreCase)) {
        return $path
    }
    return Join-Path $path "VidoSwift"
}

function Write-InstallProgress([int]$Percent, [string]$Status) {
    if (-not $ProgressFile) { return }
    try {
        [IO.File]::WriteAllText($ProgressFile, "$Percent|$Status", (New-Object Text.UTF8Encoding($false)))
    } catch {
        # Progress is only a display hint. A transient UI read lock must not interrupt installation.
    }
}

if ($InstallPath) {
    $target = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($InstallPath))
} else {
    $default = Join-Path $env:LOCALAPPDATA "Programs\VidoSwift"
    New-Item -ItemType Directory -Path $default -Force | Out-Null
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = "Choose the VidoSwift install folder. Application files will stay together inside a VidoSwift folder."
    $dialog.SelectedPath = $default
    $dialog.ShowNewFolderButton = $true
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 0 }
    $target = Resolve-VidoSwiftInstallTarget $dialog.SelectedPath
}

Write-InstallProgress 3 "Preparing installation…"
New-Item -ItemType Directory -Path $target -Force | Out-Null
$sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$files = @(Get-ChildItem -LiteralPath $PSScriptRoot -File -Recurse | Where-Object { $_.Name -ne "install.ps1" })
if (-not ($files | Where-Object { $_.Name -eq "VidoSwift.exe" })) { throw "VidoSwift.exe is missing from the installer payload." }
if (-not ($files | Where-Object { $_.FullName -eq (Join-Path $PSScriptRoot "uninstall.ps1") })) { throw "uninstall.ps1 is missing from the installer payload." }
$count = $files.Count
$copied = 0
foreach ($file in $files) {
    $relative = $file.FullName.Substring($sourceRoot.Length)
    $destination = Join-Path $target $relative
    $parent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    $copied++
    $percent = 5 + [int][Math]::Floor(($copied / [Math]::Max(1, $count)) * 88)
    Write-InstallProgress $percent "Copying application files…"
}

Write-InstallProgress 96 "Creating Desktop shortcut…"
$shell = New-Object -ComObject WScript.Shell
$desktopLinkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "VidoSwift Video Downloader.lnk"
$shortcut = $shell.CreateShortcut($desktopLinkPath)
$shortcut.TargetPath = Join-Path $target "VidoSwift.exe"
$shortcut.WorkingDirectory = $target
$shortcut.Description = "VidoSwift Video Downloader"
$shortcut.Save()

$keyPath = "Software\Microsoft\Windows\CurrentVersion\Uninstall\VidoSwift"
$key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($keyPath)
$key.SetValue("DisplayName", "VidoSwift Video Downloader")
$key.SetValue("DisplayVersion", "1.0.0")
$key.SetValue("InstallLocation", $target)
$key.SetValue("DisplayIcon", (Join-Path $target "VidoSwift.exe"))
$key.SetValue("UninstallString", "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $target 'uninstall.ps1')`"")
$key.SetValue("NoModify", 1, [Microsoft.Win32.RegistryValueKind]::DWord)
$key.SetValue("NoRepair", 1, [Microsoft.Win32.RegistryValueKind]::DWord)
$key.Close()
Write-InstallProgress 100 "Installation complete."

if (-not $Quiet) {
    [System.Windows.Forms.MessageBox]::Show("VidoSwift Video Downloader is installed in:`n$target`n`nUse the VidoSwift desktop icon to open it.", "VidoSwift Setup", "OK", "Information") | Out-Null
    Start-Process -FilePath (Join-Path $target "VidoSwift.exe") -WorkingDirectory $target
}
