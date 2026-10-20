# VidoSwift Mobile

This directory is the isolated Android/API workspace. The Windows desktop project remains in the repository root and is not imported or edited by the mobile service.

## Initial architecture

- `android-app/` will use Capacitor with React/Vite. It will keep a mobile-specific API adapter instead of `window.pywebview.api`, and carry its own mobile layout while reusing VidoSwift design tokens and assets.
- `api/` is a FastAPI service. Its analysis path uses the repository's pinned yt-dlp version and YouTube client compatibility logic. It is independent of pywebview and desktop settings.
- A later phase will add authenticated download jobs, a Redis-backed worker, temporary file storage with expiry, and a short-lived download URL. The Android app will save completed files through Android's system download/storage APIs.

## Phase 1–2 status

The initial API service currently provides `GET /v1/health` and `POST /v1/analyze`, public URL validation, a version-aware YouTube client selection, and optional Node.js 22+ discovery for yt-dlp JavaScript challenges. See [`api/README.md`](api/README.md) for local development and the current limitations.

Do not expose this initial service to the public internet yet. Authentication, per-user quotas/rate limits, durable download jobs, server egress controls, and temporary-file cleanup are required before public deployment. Browser cookie values are not accepted by this API.
