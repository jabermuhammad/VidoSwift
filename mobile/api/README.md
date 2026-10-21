# VidoSwift Mobile API

FastAPI API for the Android client. It is kept under `mobile/api/` and does not import the Windows desktop `backend/app.py` or pywebview bridge.

## Endpoints

- `GET /v1/health` — service status and the installed yt-dlp version.
- `POST /v1/analyze` — validate a public HTTP(S) URL and return video metadata and available formats.
- `GET /v1/download?url=<encoded-url>&quality=720p` — download the selected quality to a temporary server file and stream it to the app.
- `HEAD /v1/download?url=<encoded-url>&quality=720p` — validate the endpoint, URL, and quality before the app starts a download; it does not download media.
- `GET /docs` — local OpenAPI/Swagger UI.

`POST /v1/analyze` request:

```json
{"url":"https://www.youtube.com/watch?v=VIDEO_ID"}
```

Success response follows the desktop UI's video shape:

```json
{"ok":true,"video":{"id":"…","title":"…","formats":[],"heights":[]}}
```

Errors use a stable envelope:

```json
{"error":{"code":"invalid_url","message":"…","retryable":false}}
```

## Local development

Use Python 3.12+ and a virtual environment. From this directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:VIDOSWIFT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,https://localhost,capacitor://localhost"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

For YouTube JavaScript challenges, install Node.js 22+ on the API host and make `node` available on `PATH`; alternatively set `VIDOSWIFT_NODE` to its executable. The service records only whether that runtime is available, not its path.

## Safety and deployment boundary

- Only HTTP(S) URLs without embedded credentials are accepted. Localhost, private/reserved IPs, nonstandard ports, and DNS names resolving to non-public IPs are rejected.
- Analysis does not read or accept browser cookies. Raw cookie values must never be sent to the API.
- Download jobs are limited to one concurrent job per service process by default (`MAX_DOWNLOAD_JOBS` can raise this limit). Files are streamed after yt-dlp writes them to temporary storage, then deleted when the response completes.
- Downloads consume server CPU, disk, memory, and outbound bandwidth. This service still needs user authentication, per-user quotas, and deployment-level egress controls before broad public use; no secret is embedded in the APK.
- This initial service has no user authentication or quotas. Keep it on loopback/private development networks. Before public deployment, add real user authentication (no static secret embedded in the APK), rate/concurrency quotas, egress controls that also cover redirects and extractor requests, bounded job workers, temporary-file expiry, and HTTPS.
- URL DNS validation is a first guard; it does not alone prevent DNS rebinding or a media extractor following a redirect to a private address. Enforce network egress restrictions at the deployment layer before accepting untrusted requests publicly.
- Only analyze or save material the user has permission to access and retain. The service does not bypass platform access checks.
