from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Iterator
from functools import lru_cache
from urllib.parse import urlparse

from yt_dlp import YoutubeDL
from yt_dlp.version import CHANNEL, __version__

from app.security.url_validation import validate_public_video_url


logger = logging.getLogger("vidoswift.mobile_api.analyzer")
YOUTUBE_FIX_VERSION = (2026, 10, 10)
YOUTUBE_AUTH_ERRORS = (
    "login_required",
    "private video",
    "members-only",
    "members only",
    "age-restricted",
)


class _YtDlpDiagnostics:
    """Collect safe, evidence-specific signals without retaining raw yt-dlp text.

    Request-stage labels mean yt-dlp attempted that stage. They do not mean the
    request failed. Mentions of PO Tokens or JavaScript challenges are likewise
    kept distinct from explicit requirements or missing-runtime errors.
    """

    _EVENTS = (
        ("http_error_text_match_429", re.compile(r"\bhttp\s+error\s+429\s*:\s*too many requests\b", re.I)),
        ("http_error_text_match_403", re.compile(r"\bhttp\s+error\s+403\s*:\s*forbidden\b", re.I)),
        ("bot_verification_message", re.compile(r"sign in to confirm.{0,80}not a bot|not a bot", re.I)),
        ("consent_message", re.compile(r"consent.{0,80}(?:required|needed|confirm)|(?:required|needed).{0,80}consent", re.I)),
        ("po_token_requirement_observed", re.compile(r"po token.{0,80}\b(?:required|missing|needed)\b|\b(?:required|missing|needed)\b.{0,80}po token", re.I)),
        ("po_token_mentioned", re.compile(r"po token", re.I)),
        ("js_runtime_unavailable", re.compile(r"no supported javascript runtime could be found|could not find (?:a )?javascript runtime|javascript runtime.{0,50}(?:not found|unavailable)", re.I)),
        ("js_challenge_mentioned", re.compile(r"javascript challenge|js challenge", re.I)),
        ("player_response_missing", re.compile(r"failed to extract any player response", re.I)),
        ("initial_response_missing", re.compile(r"unable to extract initial player response", re.I)),
        ("player_version_missing", re.compile(r"unable to extract player version", re.I)),
        ("player_response_json_invalid", re.compile(r"failed to parse json|jsondecodeerror", re.I)),
        ("player_responses_invalid", re.compile(r"all player responses are invalid", re.I)),
        ("player_response_status_error", re.compile(r"player response playability status: (?:error|unplayable|login_required)", re.I)),
        ("player_api_attempted", re.compile(r"player api", re.I)),
        ("webpage_request_attempted", re.compile(r"downloading webpage", re.I)),
    )

    def __init__(self) -> None:
        self.events: list[str] = []

    def _record(self, message: str) -> None:
        for label, pattern in self._EVENTS:
            if pattern.search(message) and label not in self.events:
                self.events.append(label)

    def debug(self, message: str) -> None:
        self._record(message)

    def info(self, message: str) -> None:
        self._record(message)

    def warning(self, message: str) -> None:
        self._record(message)

    def error(self, message: str) -> None:
        self._record(message)


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
    """Use yt-dlp's own default client set, without forcing extra client requests."""
    if _version_tuple(version) >= YOUTUBE_FIX_VERSION:
        clients = ["default"]
    else:
        clients = ["default", "-tv_downgraded"]
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


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield wrapped yt-dlp exceptions and their underlying causes safely."""
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for linked in (getattr(current, "cause", None), current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1 and isinstance(exc_info[1], BaseException):
            pending.append(exc_info[1])


def _exception_chain_types(exc: BaseException) -> str:
    """Return exception class names only; never log raw URLs or response text."""
    return ">".join(type(item).__name__ for item in _exception_chain(exc))


def _http_status_from_exception(exc: BaseException) -> int | None:
    """Return only a status carried by a structured response/HTTP exception."""
    for item in _exception_chain(exc):
        candidates = (
            getattr(item, "code", None),
            getattr(item, "status", None),
            getattr(item, "status_code", None),
            getattr(getattr(item, "response", None), "status_code", None),
        )
        for value in candidates:
            if isinstance(value, int) and 100 <= value <= 599:
                return value
    return None


def _failure_stage(exc: BaseException, events: list[str]) -> str:
    """Report the stage named by the exception, falling back to observed attempts."""
    messages = " ".join(str(item).lower() for item in _exception_chain(exc))
    if "failed to extract any player response" in messages:
        return "player_response"
    if "unable to extract initial player response" in messages:
        return "initial_player_response"
    if "unable to extract player version" in messages:
        return "player_version"
    if "player_api_attempted" in events:
        return "player_api"
    if "webpage_request_attempted" in events:
        return "webpage"
    if "js_challenge_mentioned" in events:
        return "javascript_challenge"
    return "unknown"


def _analysis_failure(exc: Exception) -> AnalysisError:
    """Translate yt-dlp failures without labeling every YouTube rejection as login-only."""
    message = str(exc).lower()
    http_status = _http_status_from_exception(exc)
    if any(marker in message for marker in ("video unavailable", "video is unavailable", "has been removed", "this video does not exist")):
        return AnalysisError(
            "video_unavailable",
            "YouTube reports that this video is unavailable. It may have been removed or restricted in this region.",
            status_code=422,
        )
    if "sign in to confirm" in message and any(marker in message for marker in ("not a bot", "you're not", "you’re not")):
        return AnalysisError(
            "youtube_request_blocked",
            "YouTube refused this server's unauthenticated request. This can affect public videos and does not prove the video needs sign-in. Retry later; browser cookies are not automatically required.",
            status_code=503,
            retryable=True,
        )
    if "consent" in message and any(marker in message for marker in ("required", "confirm", "cookie")):
        return AnalysisError(
            "youtube_consent_required",
            "YouTube returned a consent check that this server cannot complete. Retry later; this does not prove the video itself requires sign-in.",
            status_code=503,
            retryable=True,
        )
    if http_status == 429:
        return AnalysisError(
            "source_rate_limited",
            "YouTube is rate-limiting requests from this server. Wait before retrying.",
            status_code=429,
            retryable=True,
        )
    if http_status == 403:
        return AnalysisError(
            "youtube_request_blocked",
            "YouTube rejected an extraction request. This may be a temporary platform or network restriction and does not necessarily mean the video requires sign-in.",
            status_code=503,
            retryable=True,
        )
    if "po token" in message and any(word in message for word in ("required", "missing", "unavailable", "needed")):
        return AnalysisError(
            "po_token_required",
            "YouTube requires a format access token for this extraction path. This server is not configured with one; retrying or changing video quality may help when YouTube offers another format.",
            status_code=503,
            retryable=True,
        )
    if "failed to extract any player response" in message:
        return AnalysisError(
            "youtube_player_response_missing",
            "YouTube did not return a usable player response. This can result from a temporary extractor, consent, verification, or network issue; it does not by itself indicate a private video or confirm IP blocking.",
            status_code=503,
            retryable=True,
        )
    if any(marker in message for marker in ("javascript runtime", "js challenge", "could not find a javascript runtime")):
        return AnalysisError(
            "javascript_runtime_required",
            "YouTube extraction needs a supported JavaScript runtime. Install Node.js 22 or newer on the backend and restart it.",
            status_code=503,
        )
    if any(marker in message for marker in ("failed to resolve", "name or service not known", "temporary failure in name resolution")):
        return AnalysisError(
            "source_network_error",
            "The backend could not resolve or reach YouTube. Check DNS and outbound network access on the server.",
            status_code=503,
            retryable=True,
        )
    if any(marker in message for marker in YOUTUBE_AUTH_ERRORS):
        return AnalysisError(
            "authentication_required",
            "YouTube reports that this video is restricted and may require an authorized signed-in session.",
            status_code=422,
        )
    if "no formats" in message or "requested format is not available" in message:
        return AnalysisError(
            "formats_unavailable",
            "YouTube returned no formats usable by this server. The video may be region-restricted, require a format token, or be temporarily unavailable.",
            status_code=422,
            retryable=True,
        )
    if "timed out" in message or isinstance(exc, TimeoutError):
        return AnalysisError(
            "analysis_timeout",
            "Video analysis timed out. Retry the request.",
            status_code=504,
            retryable=True,
        )
    return AnalysisError(
        "analysis_failed",
        "The video platform did not return usable video information.",
        retryable=True,
    )


def analyze_video(raw_url: str) -> dict:
    started_at = time.monotonic()
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
        diagnostics = _YtDlpDiagnostics()
        options["logger"] = diagnostics
        # Enable yt-dlp debug callbacks, but retain only the safe labels above.
        options["verbose"] = True
        if runtime := find_youtube_js_runtime():
            options["js_runtimes"] = {"node": {"path": runtime}}

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        failure = _analysis_failure(exc)
        http_status = _http_status_from_exception(exc)
        http_text_matches = ",".join(
            code for event, code in (
                ("http_error_text_match_429", "429"),
                ("http_error_text_match_403", "403"),
            ) if is_youtube and event in diagnostics.events
        ) if is_youtube else ""
        final_stage = _failure_stage(exc, diagnostics.events) if is_youtube else "unknown"
        logger.warning(
            "video analysis failed host=%s failure_code=%s exception_type=%s yt_dlp_exception_chain=%s yt_dlp_stage_inferred=%s yt_dlp_http_status=%s yt_dlp_http_error_text_matches=%s yt_dlp_events=%s elapsed_ms=%d",
            urlparse(url).hostname,
            failure.code,
            type(exc).__name__,
            _exception_chain_types(exc),
            final_stage,
            str(http_status) if http_status is not None else "unavailable",
            http_text_matches or "none",
            ",".join(diagnostics.events) if is_youtube else "",
            round((time.monotonic() - started_at) * 1000),
        )
        raise failure from exc

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
