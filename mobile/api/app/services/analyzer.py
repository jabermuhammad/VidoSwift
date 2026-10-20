from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from functools import lru_cache
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.version import CHANNEL, __version__

from app.security.url_validation import validate_public_video_url


logger = logging.getLogger("vidoswift.mobile_api.analyzer")
YOUTUBE_FIX_VERSION = (2026, 10, 10)
YOUTUBE_AUTH_ERRORS = (
    "sign in",
    "login_required",
    "not a bot",
    "confirm you're not",
    "confirm you’re not",
)


class AnalysisError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 502, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def _version_tuple(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in re.findall(r"\d+", value)[:3]]
    return tuple((parts + [0, 0, 0])[:3])  # type: ignore[return-value]


def youtube_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def youtube_extractor_args(version: str = __version__) -> dict:
    """Carry forward VidoSwift's tv_downgraded workaround and embed fallback."""
    if _version_tuple(version) >= YOUTUBE_FIX_VERSION:
        clients = ["default", "web_embedded"]
    else:
        clients = ["default", "-tv_downgraded", "web_embedded"]
    return {"youtube": {"player_client": clients}}


@lru_cache(maxsize=1)
def find_youtube_js_runtime() -> str | None:
    """Use the bundled/configured Node runtime only when it is Node 22+."""
    configured = os.environ.get("VIDOSWIFT_NODE")
    runtime = configured if configured and os.path.isfile(configured) else shutil.which("node")
    if not runtime:
        return None
    try:
        completed = subprocess.run(
            [runtime, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", completed.stdout or completed.stderr)
    return runtime if match and int(match.group(1)) >= 22 else None


def _format_info(item: dict) -> dict:
    height = item.get("height")
    return {
        "format_id": str(item.get("format_id", "")),
        "ext": str(item.get("ext", "")),
        "height": height,
        "resolution": item.get("resolution") or (f"{height}p" if height else "Audio"),
        "note": str(item.get("format_note") or ""),
        "filesize": item.get("filesize") or item.get("filesize_approx"),
        "has_video": item.get("vcodec") not in (None, "none"),
        "has_audio": item.get("acodec") not in (None, "none"),
    }


def _duration_text(seconds) -> str:
    if not isinstance(seconds, (int, float)):
        return "Unavailable"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"
    return f"{seconds // 60:02}:{seconds % 60:02}"


def _size_text(value) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return "Unavailable"
    size = float(value)
    units = ("B", "KB", "MB", "GB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return "Unavailable"


def analyze_video(raw_url: str) -> dict:
    url = validate_public_video_url(raw_url)
    is_youtube = youtube_host(url)
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 10,
        "retries": 1,
        "extractor_retries": 1,
        "skip_download": True,
        "ignore_no_formats_error": True,
    }
    if is_youtube:
        options["extractor_args"] = youtube_extractor_args()
        if runtime := find_youtube_js_runtime():
            options["js_runtimes"] = {"node": {"path": runtime}}

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        message = str(exc).lower()
        logger.warning("video analysis failed host=%s exception_type=%s", urlparse(url).hostname, type(exc).__name__)
        if any(marker in message for marker in YOUTUBE_AUTH_ERRORS):
            raise AnalysisError(
                "authentication_required",
                "The platform requires a signed-in session or does not allow this request.",
                status_code=422,
            ) from exc
        if "timed out" in message or isinstance(exc, TimeoutError):
            raise AnalysisError(
                "analysis_timeout",
                "Video analysis timed out. Retry the request.",
                status_code=504,
                retryable=True,
            ) from exc
        raise AnalysisError(
            "analysis_failed",
            "The video platform did not return usable video information.",
            retryable=True,
        ) from exc

    if not info:
        raise AnalysisError("analysis_failed", "The video platform returned no video information.")

    formats = [
        _format_info(item)
        for item in info.get("formats", [])
        if item.get("format_id") and (item.get("vcodec") != "none" or item.get("acodec") != "none")
    ]
    if is_youtube and not formats:
        raise AnalysisError(
            "formats_unavailable",
            "YouTube returned no downloadable formats for this video.",
            status_code=422,
        )

    heights = sorted({item["height"] for item in formats if item["height"]}, reverse=True)
    sizes = [item["filesize"] for item in formats if isinstance(item["filesize"], (int, float)) and item["filesize"] > 0]
    duration = info.get("duration")
    estimated_size = max(sizes) if sizes else None
    return {
        "ok": True,
        "video": {
            "id": str(info.get("id") or ""),
            "title": str(info.get("title") or "Untitled video"),
            "thumbnail": str(info.get("thumbnail") or ""),
            "duration": duration,
            "duration_text": _duration_text(duration),
            "extractor": str(info.get("extractor_key") or info.get("extractor") or "Video"),
            "uploader": str(info.get("uploader") or info.get("channel") or ""),
            "view_count": info.get("view_count"),
            "formats": formats,
            "heights": heights,
            "estimated_size": estimated_size,
            "estimated_size_text": _size_text(estimated_size),
        },
    }
