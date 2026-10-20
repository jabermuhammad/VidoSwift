$ErrorActionPreference = "Continue"
Add-Type -AssemblyName System.Windows.Forms
if ([System.Windows.Forms.MessageBox]::Show("Remove VidoSwift Video Downloader? Downloaded videos and your download folder will be kept.", "Uninstall VidoSwift", "YesNo", "Question") -ne [System.Windows.Forms.DialogResult]::Yes) { exit 0 }

$target = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$desktopLinkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "VidoSwift Video Downloader.lnk"
if (Test-Path -LiteralPath $desktopLinkPath) {
    try {
        $shell = New-Object -ComObject WScript.Shell
        $desktopLink = $shell.CreateShortcut($desktopLinkPath)
        $linkTarget = [IO.Path]::GetFullPath($desktopLink.TargetPath)
        if ([string]::Equals($linkTarget, (Join-Path $target "VidoSwift.exe"), [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $desktopLinkPath -Force
        }
    } catch { }
}

Remove-Item -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VidoSwift" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $target "VidoSwift.exe") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $target "_internal") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $target "uninstall.ps1") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $target "THIRD-PARTY-NOTICES.md") -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $target "vidoswift-build-info.json") -Force -ErrorAction SilentlyContinue
if ((Test-Path -LiteralPath $target) -and -not (Get-ChildItem -LiteralPath $target -Force -ErrorAction SilentlyContinue)) {
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
}
[System.Windows.Forms.MessageBox]::Show("VidoSwift was removed. Downloaded files were left in place.", "Uninstall VidoSwift", "OK", "Information") | Out-Null
