# VidoSwift Video Downloader

Windows desktop application using the provided React UI in a pywebview window and the existing Python/yt-dlp download engine.

## Run from source

1. Install Python 3.12+, Node.js 20.19+ and pnpm 10+.
2. In `backend`, install Python packages from `requirements.txt` (the build uses a vendored copy under `backend/vendor`).
3. From this project directory, run `build.ps1` in PowerShell. Set `$env:VIDOSWIFT_PYTHON` if the Python launcher is not installed.
4. Run `release\VidoSwift.exe`.

The desktop window uses Microsoft's WebView2 engine. Windows installations normally include the Evergreen WebView2 Runtime; if it is missing, install that runtime from Microsoft's official WebView2 page.

## Architecture and safety

- `ui` contains the original Vite/React design. The production UI files are bundled with the Python application.
- `backend\app.py` exposes a small pywebview JavaScript bridge to yt-dlp. The UI never constructs or executes shell commands.
- URL scheme, credentials, local hostnames, and non-public IP addresses are rejected before extraction.
- Settings and download history are stored in `%APPDATA%\VidoSwift\state.json`.
- Downloads default to `%USERPROFILE%\Downloads\VidoSwift` and are checked for a non-empty output file before success is recorded.
- Downloads require internet access. Some sites/formats may change or require FFmpeg for stream merging or MP3 conversion.
- Optional YouTube browser-cookie access is off by default and limited to YouTube URLs. The user selects a supported browser and confirms permission in Settings; yt-dlp reads the selected local browser profile at request time. VidoSwift does not export or store cookie values, and does not log raw yt-dlp diagnostics. YouTube may still require a normal sign-in or refuse a request; VidoSwift does not bypass those checks.
- The installer is per-user, creates a Start Menu shortcut and uninstall entry, and leaves the download folder untouched on uninstall.

## Build outputs

- `release\VidoSwift.exe` — PyInstaller standalone build.
- `outputs\VidoSwift-Setup.exe` — self-extracting .NET Framework per-user installer built by `installer\build-installer.ps1`.

`installer\install.ps1` and `installer\uninstall.ps1` are the installer source files. `backend\VidoSwift.spec` is the PyInstaller build recipe.
