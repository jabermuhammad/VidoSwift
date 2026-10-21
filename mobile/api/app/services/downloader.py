from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from threading import BoundedSemaphore
from urllib.parse import urlparse

from yt_dlp import YoutubeDL

from app.security.url_validation import validate_public_video_url
from app.services.analyzer import find_youtube_js_runtime, youtube_extractor_args, youtube_host


logger = logging.getLogger("vidoswift.mobile_api.downloader")
MAX_DOWNLOAD_JOBS = max(1, int(os.environ.get("MAX_DOWNLOAD_JOBS", "1")))
DOWNLOAD_SEMAPHORE = BoundedSemaphore(MAX_DOWNLOAD_JOBS)
QUALITY_HEIGHTS = {"360p": 360, "480p": 480, "720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160}
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
}


class DownloadError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 422, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def _quality_format_selector(quality: str, *, ffmpeg_available: bool) -> str:
    """Select a format at or below the requested height, merging streams only with FFmpeg."""
    if quality == "best":
        return "bestvideo*+bestaudio/best" if ffmpeg_available else "best[ext=mp4]/best"

    height = QUALITY_HEIGHTS[quality]
    if ffmpeg_available:
        # yt-dlp's documented split-stream selector, with a combined-format fallback.
        return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
    # Without FFmpeg, accept only a pre-merged audio+video format and never exceed the cap.
    return f"best[height<={height}][ext=mp4]/best[height<={height}]"


def download_video(raw_url: str, quality: str) -> tuple[Path, Path, str, str]:
    url = validate_public_video_url(raw_url)
    ffmpeg_available = shutil.which("ffmpeg") is not None
    selector = _quality_format_selector(quality, ffmpeg_available=ffmpeg_available)

    if not DOWNLOAD_SEMAPHORE.acquire(blocking=False):
        raise DownloadError("download_busy", "A download is already running. Wait briefly and retry.", status_code=429, retryable=True)

    temp_dir: Path | None = None
    try:
        temp_dir = Path(tempfile.mkdtemp(prefix="vidoswift-mobile-"))
    except OSError as exc:
        DOWNLOAD_SEMAPHORE.release()
        raise DownloadError("storage_unavailable", "The server could not create temporary download storage.", status_code=503, retryable=True) from exc
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 1,
        "ignoreerrors": False,
        "restrictfilenames": True,
        "format": selector,
        "outtmpl": str(temp_dir / "%(title).100B-%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "continuedl": True,
        "progress": False,
    }
    if youtube_host(url):
        options["extractor_args"] = youtube_extractor_args()
        if runtime := find_youtube_js_runtime():
            options["js_runtimes"] = {"node": {"path": runtime}}

    try:
        with YoutubeDL(options) as ydl:
            ydl.download([url])
        files = [path for path in temp_dir.iterdir() if path.is_file() and not path.name.endswith((".part", ".ytdl", ".json"))]
        media = [path for path in files if path.suffix.lower() in MEDIA_TYPES]
        if not media:
            raise DownloadError(
                "media_file_missing",
                "The source returned no playable video file. This format may require FFmpeg on the server.",
                status_code=503,
            )
        path = max(media, key=lambda item: item.stat().st_size)
        safe_stem = re.sub(r"[^A-Za-z0-9._ -]", "", path.stem).strip().replace(" ", "_")[:100] or "vidoswift-video"
        return path, temp_dir, MEDIA_TYPES[path.suffix.lower()], f"{safe_stem}{path.suffix.lower()}"
    except DownloadError:
        if temp_dir:
            cleanup_download(temp_dir)
        raise
    except Exception as exc:
        if temp_dir:
            cleanup_download(temp_dir)
        message = str(exc).lower()
        host = urlparse(url).hostname or "unknown"
        logger.warning("video download failed host=%s exception_type=%s", host, type(exc).__name__)
        if "429" in message or "too many requests" in message:
            raise DownloadError("source_rate_limited", "The video platform is rate-limiting downloads. Wait and retry.", status_code=429, retryable=True) from exc
        if "ffmpeg" in message:
            raise DownloadError("ffmpeg_required", "This quality requires FFmpeg, which is not installed on the API server.", status_code=503) from exc
        if "requested format is not available" in message:
            label = "Best available" if quality == "best" else f"{QUALITY_HEIGHTS[quality]}p or lower"
            raise DownloadError(
                "quality_unavailable",
                f"No downloadable audio/video format is available at {label}. Re-analyze and choose another listed quality, or try Best available. Separate video and audio streams can only be merged when FFmpeg is installed.",
                status_code=422,
            ) from exc
        if any(marker in message for marker in ("sign in", "login_required", "not a bot", "confirm you're not", "confirm you’re not")):
            is_bot_check = "not a bot" in message or "confirm you're not" in message or "confirm you’re not" in message
            if is_bot_check:
                raise DownloadError(
                    "youtube_bot_verification",
                    "YouTube asked this server to complete a bot check. This may affect public videos; the API cannot complete that check automatically. Try again later.",
                    status_code=503,
                    retryable=True,
                ) from exc
            raise DownloadError("authentication_required", "The platform requires a signed-in session or restricts server downloads.", status_code=422) from exc
        raise DownloadError("download_failed", "The source rejected the download or the selected format is unavailable.", status_code=422, retryable=True) from exc
    finally:
        DOWNLOAD_SEMAPHORE.release()


def cleanup_download(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
