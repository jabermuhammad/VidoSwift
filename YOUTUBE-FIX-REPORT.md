# VidoSwift — "The page needs to be reloaded." (YouTube) — 2026-10-18

Apply: `cd <your project root> && patch -p1 < youtube-client-fix.patch`   (paths are backend/..., build.ps1, tools/...)
Then on Windows: `.\tools\Refresh-YtDlpVendor.ps1` (full yt-dlp refresh) and `.\build.ps1`.

## Cause (evidence)
* Vendored `_base.py`/`_video.py`/`version.py` are byte-identical to the stock yt-dlp 2026.08.19 wheel (cmp).
* In 2026.08.19, signed-in sessions default to clients `web_embedded, tv_downgraded, web`; signed-out ones never include
  `tv_downgraded` (offline run of yt-dlp's own `_get_requested_clients`, `tools/client_selection.py`).
* Upstream issue #17389: `tv_downgraded` playability UNPLAYABLE -> "The page needs to be reloaded." Fixed on master by
  PRs #17723/#17805 (Oct 10 2026): `tv_samsung` replaces it. Verified in the real nightly 2026.10.10.233141 wheel:
  authed defaults are `web_embedded, tv_samsung, web`. PyPI's latest STABLE is still 2026.08.19 (nightly only).
* Therefore the error is expected when VidoSwift's browser-cookie option is on (signed-in session). NOT confirmed for the
  screenshot itself: which EXE/cookie setting produced it is unknown, and no live YouTube call was possible here.

## Changes
* app.py: `youtube_extractor_args()` (excludes `tv_downgraded` when bundled yt-dlp < 2026.10.10, nothing when newer);
  `ytdlp_options()` shared by analysis AND download; `DiagnosticLogger`/`DiagnosticYoutubeDL` record client attempts and
  playability status (sanitized, bounded); request log (app build, yt-dlp version/channel, domain, video id, cookie mode,
  extractor args); clients actually used on success; home-dir redaction in path logs; "page needs to be reloaded" is
  translated to an actionable message (no bypass); build id `1.0.0-youtube-client-fix-20261018.1`.
* requirements.txt: pin `yt-dlp[default]==2026.10.10.233141.dev0` (yt-dlp-ejs stays 0.8.0).
* build.ps1: warns if bundled yt-dlp predates the fix. tools/Refresh-YtDlpVendor.ps1: complete vendor swap (keeps backup).
* tests: 35 passing (was 23 collected + 1 error that came from my earlier os.name patch; fixed via `IS_WINDOWS`).

## Not verified
Live YouTube metadata/download (sandbox proxy returns 403), cookie authentication, Windows EXE build/launch, installer,
Refresh-YtDlpVendor.ps1 / build.ps1 edits (not executed; no PowerShell here). Old-version workaround (`web_embedded, web`)
may still hit PO-token/embedding limits on some videos; the nightly refresh is the real fix.
