from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import ipaddress
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
VENDOR_DIR = BASE_DIR / "vendor"
if VENDOR_DIR.is_dir():
    sys.path.insert(0, str(VENDOR_DIR))

import webview
from yt_dlp import YoutubeDL
from yt_dlp import cookies as yt_dlp_cookies
from yt_dlp.version import CHANNEL as YT_DLP_CHANNEL
from yt_dlp.version import __version__ as YT_DLP_VERSION
from yt_dlp.utils import DownloadCancelled

APP_TITLE = "VidoSwift Video Downloader"
APP_BUILD_VERSION = "1.0.0-chrome-cookie-dpapi-fix-20261020.1"
APP_DATA = Path(os.environ.get("APPDATA", Path.home())) / "VidoSwift"
STATE_FILE = APP_DATA / "state.json"
DEFAULT_FOLDER = Path.home() / "Downloads" / "VidoSwift"
DIAGNOSTIC_LOG = APP_DATA / "startup.log"
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_FORMATS = {"mp4", "webm", "mkv", "mp3"}
ALLOWED_QUALITIES = {"best", "1080p", "720p", "480p", "audio"}
SUPPORTED_COOKIE_BROWSERS = {"brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale"}


def youtube_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com") or host == "youtube-nocookie.com" or host.endswith(".youtube-nocookie.com")


def youtube_video_id(url: str) -> str:
    for pattern in (r"[?&]v=([\w-]{11})", r"(?:youtu\.be|/shorts|/embed|/live|/v)/([\w-]{11})"):
        match = re.search(pattern, url or "")
        if match:
            return match.group(1)
    return "unknown"


# Older yt-dlp releases request YouTube's broken tv_downgraded player for signed-in
# sessions. Upstream replaced it with tv_samsung in the 2026-10-10 nightly.
YT_DLP_YOUTUBE_FIX_VERSION = (2026, 10, 10)


def version_tuple(value) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", str(value))[:4])


def youtube_extractor_args(version=None) -> dict:
    """Avoid the broken legacy player and add the no-PO-token embed client as fallback."""
    installed = YT_DLP_VERSION if version is None else version
    if version_tuple(installed) >= YT_DLP_YOUTUBE_FIX_VERSION:
        return {"youtube": {"player_client": ["default", "web_embedded"]}}
    return {"youtube": {"player_client": ["default", "-tv_downgraded", "web_embedded"]}}


def find_youtube_js_runtime() -> str | None:
    """Find and validate the bundled Node runtime, with a PATH fallback."""
    name = "node.exe" if os.name == "nt" else "node"
    bundled = BASE_DIR / "bin" / name
    runtime = str(bundled) if bundled.is_file() else shutil.which("node")
    if not runtime:
        log_stage("youtube_js_runtime", "unavailable", "Node.js 22+ was not found")
        return None
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run([runtime, "--version"], capture_output=True, text=True,
                                timeout=5, check=True, creationflags=creationflags)
        match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout or result.stderr)
        if not match or int(match.group(1)) < 22:
            log_stage("youtube_js_runtime", "unsupported", "Node.js 22+ is required")
            return None
        log_stage("youtube_js_runtime", "ready", f"version={match.group(0)}")
        return runtime
    except Exception as exc:
        log_stage("youtube_js_runtime", "failed", f"error={type(exc).__name__}")
        return None


def ytdlp_options(url: str, socket_timeout: int) -> dict:
    """Options shared by metadata analysis and download."""
    options = {"quiet": True, "no_warnings": True, "noprogress": True,
               "logger": QuietLogger(), "noplaylist": True, "socket_timeout": socket_timeout}
    if youtube_host(url):
        options["extractor_args"] = youtube_extractor_args()
        options["logger"] = DiagnosticLogger()
        if runtime := find_youtube_js_runtime():
            options["js_runtimes"] = {"node": {"path": runtime}}
    return options


def youtube_analysis_options(url: str) -> dict:
    """Keep metadata analysis responsive while allowing one transient retry."""
    options = ytdlp_options(url, 10)
    options.update({
        "skip_download": True,
        "ignore_no_formats_error": True,
        "retries": 1,
        "extractor_retries": 1,
    })
    return options


def log_youtube_clients_used(stage: str, info) -> None:
    try:
        clients = sorted({str(fmt.get("__yt_dlp_client"))
                          for fmt in (info or {}).get("formats", [])
                          if fmt.get("__yt_dlp_client")})
        if clients:
            log_stage(stage, "clients_used", ",".join(clients))
        else:
            log_stage(stage, "clients_used", "not_reported")
    except Exception:
        log_stage(stage, "clients_used", "unavailable")


def log_youtube_request(stage: str, url: str, cookie_browser) -> None:
    if youtube_host(url):
        log_stage(stage, "request",
                  f"app_version={APP_BUILD_VERSION} yt_dlp={YT_DLP_VERSION}/{YT_DLP_CHANNEL} "
                  f"video_id={youtube_video_id(url)} cookie_mode={'browser:' + cookie_browser if cookie_browser else 'off'} "
                  f"extractor_args={json.dumps(youtube_extractor_args(), sort_keys=True)}")


_log = logging.getLogger("vidoswift.startup")
_single_instance_handle = None
_TEMP_DIR_LOCK = threading.Lock()
_TEMP_DIR_READY = False
_COOKIE_DIAG_CONTEXT = threading.local()


def ensure_writable_temp_dir() -> Path:
    """Keep yt-dlp's temporary cookie DB copies in a writable per-user folder.

    Some restricted Windows launches expose an unwritable TEMP path (for example,
    an AppContainer temp directory). yt-dlp uses tempfile.TemporaryDirectory while
    reading browser cookies, so it otherwise reports a misleading cookie DB error
    for every browser. The fallback is inside VidoSwift's per-user app-data folder;
    yt-dlp still owns and removes each temporary copy.
    """
    global _TEMP_DIR_READY
    with _TEMP_DIR_LOCK:
        if _TEMP_DIR_READY:
            return Path(tempfile.gettempdir())
        APP_DATA.mkdir(parents=True, exist_ok=True)
        app_temp = APP_DATA / "temp"
        app_temp.mkdir(parents=True, exist_ok=True)
        # Avoid Windows AppContainer's virtualized TEMP path. yt-dlp may copy
        # the selected browser's cookie DB into a TemporaryDirectory; keeping
        # that transient copy under the user's VidoSwift profile also ensures
        # its cleanup follows the same user permissions.
        tempfile.tempdir = str(app_temp)
        _TEMP_DIR_READY = True
        log_stage("temp_directory", "app_data")
        return app_temp


def acquire_single_instance() -> bool:
    global _single_instance_handle
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    handle = create_mutex(None, True, "Local\\VidoSwift.VideoDownloader")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        log_stage("single_instance", "already_running")
        return False
    _single_instance_handle = handle
    log_stage("single_instance", "acquired")
    return True


def release_single_instance():
    global _single_instance_handle
    if _single_instance_handle:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex(_single_instance_handle)
        kernel32.CloseHandle(_single_instance_handle)
        _single_instance_handle = None


def configure_diagnostics():
    try:
        APP_DATA.mkdir(parents=True, exist_ok=True)
        if not _log.handlers:
            handler = RotatingFileHandler(DIAGNOSTIC_LOG, maxBytes=512_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            _log.addHandler(handler)
            _log.setLevel(logging.INFO)
            _log.propagate = False
        webview_log = logging.getLogger("pywebview")
        if not any(handler is existing for handler in webview_log.handlers for existing in _log.handlers):
            for handler in _log.handlers:
                webview_log.addHandler(handler)
        webview_log.setLevel(logging.ERROR)
        webview_log.propagate = False
    except OSError:
        pass


def log_stage(stage, status="ok", detail=""):
    if _log.handlers:
        # Redact secret-like fields and flatten messages before writing diagnostics.
        safe_detail = re.sub(r"(?i)\b(cookies?|set-cookie|authorization|password|access[_-]?token|refresh[_-]?token)\b\s*[:=].*", r"\1=[redacted]", str(detail))
        safe_detail = " ".join(safe_detail.replace("\r", " ").replace("\n", " ").split())[:300]
        _log.info("stage=%s status=%s detail=%s", stage, status, safe_detail)


def sanitize_diagnostic_text(value: str) -> str:
    """Remove credentials and signed URL parameters from diagnostic text."""
    text = str(value)
    text = re.sub(r"(?im)^\s*(cookie|set-cookie|authorization)\s*:\s*.*$", r"\1: [REDACTED]", text)
    text = re.sub(
        r"(?i)\b(cookies?|set-cookie|authorization|password|access[_-]?token|refresh[_-]?token|id[_-]?token)\b\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]", text)
    return re.sub(r"(?i)(https?://[^\s?#'\"]+)\?[^\s#'\"]*", r"\1?[query redacted]", text)


def log_exception(stage: str, exc: BaseException) -> None:
    safe_message = sanitize_diagnostic_text(str(exc))
    safe_traceback = sanitize_diagnostic_text("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    _log.error("stage=%s status=failed exception_type=%s error=%s\n%s",
               stage, type(exc).__name__, safe_message[:2000], safe_traceback[:16000])


def resolve_browser_cookie_profile(browser: str) -> tuple[str | None, str | None]:
    """Resolve the browser profile and cookie database selected by yt-dlp."""
    if browser == "firefox":
        roots = list(yt_dlp_cookies._firefox_browser_dirs())
        database = yt_dlp_cookies._newest(yt_dlp_cookies._firefox_cookie_dbs(roots))
        return (str(Path(database).parent), str(database)) if database else (None, None)
    if browser in {"chrome", "edge", "chromium", "brave", "vivaldi", "whale"}:
        config = yt_dlp_cookies._get_chromium_based_browser_settings(browser)
        database = yt_dlp_cookies._newest(
            yt_dlp_cookies._find_files(config["browser_dir"], "Cookies", QuietLogger()))
        if database:
            parent = Path(database).parent
            profile = parent.parent if parent.name.lower() == "network" else parent
            return str(profile), str(database)
        return config["browser_dir"], None
    return None, None


@contextmanager
def browser_cookie_diagnostics(browser: str | None, host: str):
    old_browser = getattr(_COOKIE_DIAG_CONTEXT, "browser", None)
    old_counters = getattr(_COOKIE_DIAG_CONTEXT, "decrypt_counters", None)
    profile_path = database_path = None
    if browser:
        log_stage("youtube_cookie_selection", "selected", f"host={host} browser={browser}")
        try:
            profile_path, database_path = resolve_browser_cookie_profile(browser)
            status = "detected" if database_path else "database_not_found"
            log_stage("browser_profile", status,
                      f"browser={browser} profile_path={profile_path or 'unavailable'} database_path={database_path or 'unavailable'}")
        except Exception as exc:
            log_exception("browser_profile_detection", exc)
            log_stage("browser_profile", "detection_failed", f"browser={browser} exception={type(exc).__name__}")
        _COOKIE_DIAG_CONTEXT.browser = browser
        _COOKIE_DIAG_CONTEXT.decrypt_counters = {"attempted": 0, "succeeded": 0, "failed": 0}
    else:
        log_stage("youtube_cookie_selection", "disabled_or_not_youtube", f"host={host} browser=none")
    try:
        yield profile_path
    finally:
        if browser:
            _COOKIE_DIAG_CONTEXT.browser = old_browser
            _COOKIE_DIAG_CONTEXT.decrypt_counters = old_counters


def load_cookie_jar_for_diagnostics(ydl, browser: str) -> None:
    """Trigger yt-dlp's normal cookie loading without logging cookie values."""
    try:
        _ = ydl.cookiejar
    except Exception as exc:
        log_exception("browser_cookie_load", exc)
        raise
    log_stage("browser_cookie_load", "success", f"browser={browser}; cookie_values=not_logged")


def run_with_cookie_lock_fallback(operation, options: dict, cookie_browser: str | None, stage: str):
    """Retry public YouTube work without cookies when Chrome cookies cannot be read."""
    try:
        return operation(options, cookie_browser)
    except Exception as exc:
        reason = str(exc).lower()
        lock_error = "could not copy chrome cookie database" in reason
        decrypt_error = "failed to decrypt with dpapi" in reason
        if not cookie_browser or not (lock_error or decrypt_error):
            raise
        fallback_options = dict(options)
        fallback_options.pop("cookiesfrombrowser", None)
        # Keep the unauthenticated public-video retry within the UI's 30 s wait.
        fallback_options.update(socket_timeout=5, retries=0, extractor_retries=0)
        fallback_reason = "database_locked" if lock_error else "dpapi_decrypt_failed"
        log_stage(f"{stage}_cookie_fallback", "retry_without_cookies",
                  f"browser={cookie_browser} reason={fallback_reason}")
        try:
            return operation(fallback_options, None)
        except Exception as fallback_exc:
            fallback_text = str(fallback_exc).lower()
            if any(marker in fallback_text for marker in (
                    "sign in", "login_required", "not a bot", "confirm you're not",
                    "no downloadable formats", "requested format is not available")):
                raise RuntimeError(
                    f"Chrome cookies could not be read ({fallback_reason}); YouTube also requires a signed-in session."
                ) from fallback_exc
            raise


def valid_url(value: str) -> str:
    url = (value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname or "." not in parsed.hostname:
        raise ValueError("Enter a valid public video URL beginning with http:// or https://.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not supported.")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".lan")):
        raise ValueError("Local network addresses are not supported.")
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise ValueError("Local and private network addresses are not supported.")
    except ValueError as exc:
        if "network addresses" in str(exc):
            raise
        try:
            resolved = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
            if any(not address.is_global for address in resolved):
                raise ValueError("Local and private network addresses are not supported.")
        except socket.gaierror:
            pass
    return url


def clean_format(item: dict) -> dict:
    return {
        "format_id": str(item.get("format_id", "")),
        "ext": str(item.get("ext", "")),
        "height": item.get("height"),
        "resolution": item.get("resolution") or (f"{item['height']}p" if item.get("height") else "Audio"),
        "note": str(item.get("format_note") or ""),
        "filesize": item.get("filesize") or item.get("filesize_approx"),
        "has_video": item.get("vcodec") not in (None, "none"),
        "has_audio": item.get("acodec") not in (None, "none"),
    }


class QuietLogger:
    """Prevent yt-dlp from writing raw extractor diagnostics to the console."""
    def debug(self, _message):
        pass

    def warning(self, _message):
        pass

    def error(self, _message):
        pass


class DiagnosticLogger(QuietLogger):
    """Capture only safe YouTube client/challenge hints; never log cookies or URLs."""
    _KEEP = re.compile(
        r"player API JSON|playability status|n challenge|PO Token|SABR|formats may be missing|"
        r"JavaScript runtime|web client https formats|no formats",
        re.IGNORECASE,
    )

    def __init__(self):
        self._lines = 0

    def _record(self, status, message):
        if self._lines >= 40:
            return
        safe = sanitize_diagnostic_text(message)
        self._lines += 1
        log_stage(f"ytdlp_{status}", "info", safe[:300])

    def debug(self, message):
        if self._KEEP.search(str(message)):
            self._record("trace", message)

    def warning(self, message):
        self._record("warning", message)

    def error(self, message):
        self._record("error", message)


class DiagnosticYoutubeDL(YoutubeDL):
    """Forward yt-dlp's selected debug lines without enabling verbose output."""
    def write_debug(self, message, only_once=False):
        logger = self.params.get("logger")
        if isinstance(logger, DiagnosticLogger):
            logger.debug(message)
        else:
            super().write_debug(message, only_once=only_once)


class VidoSwiftApi:
    def __init__(self):
        log_stage("api_init", "started")
        self._lock = threading.RLock()
        self._cancelled: set[str] = set()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vidoswift")
        self._window = None
        self._maximized = False
        self._state = {"settings": {}, "jobs": [], "history": []}
        self._ready = False
        self._init_error = None
        self._ready_event = threading.Event()
        self._executor.submit(self._initialize_state)

    def _initialize_state(self):
        try:
            log_stage("state_init", "started")
            APP_DATA.mkdir(parents=True, exist_ok=True)
            loaded_state = self._load_state()
            with self._lock:
                self._state = loaded_state
            self._recover_interrupted_jobs()
            self._ready = True
            log_stage("state_init", "ready")
        except Exception as exc:
            self._init_error = type(exc).__name__
            log_stage("state_init", "failed", self._init_error)
        finally:
            self._ready_event.set()

    def health(self):
        if self._ready:
            log_stage("bridge_health", "ready")
            return {"ok": True, "status": "ready"}
        log_stage("bridge_health", "failed" if self._init_error else "initializing", self._init_error or "")
        return {"ok": False, "status": "failed" if self._init_error else "initializing"}

    def quit_app(self):
        if self._window:
            self._window.destroy()
            return True
        return False

    def minimize_window(self):
        if self._window:
            self._window.minimize()
            return True
        return False

    def toggle_maximize_window(self):
        if not self._window:
            return False
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized
        return self._maximized

    def _recover_interrupted_jobs(self):
        with self._lock:
            for job in self._state["jobs"]:
                if job["status"] in {"Queued", "Downloading", "Processing"}:
                    job.update({"status": "Cancelled", "error": "Download interrupted when VidoSwift was closed.", "speed": "", "eta": ""})
                    self._state["history"].insert(0, {**job, "finished": time.time()})
            self._state["history"] = self._state["history"][:500]
            self._save()

    def _load_state(self):
        try:
            value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                value.setdefault("settings", {})
                value.setdefault("jobs", [])
                value.setdefault("history", [])
                return value
        except (OSError, ValueError) as exc:
            log_stage("state_read", "recovered", type(exc).__name__)
            pass
        return {"settings": {}, "jobs": [], "history": []}

    def _save(self):
        with self._lock:
            fd, temp_path = tempfile.mkstemp(prefix="state-", suffix=".json", dir=APP_DATA)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(self._state, stream, ensure_ascii=False, indent=2)
                os.replace(temp_path, STATE_FILE)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)

    def _folder(self):
        path = Path(self._state["settings"].get("folder") or DEFAULT_FOLDER).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def get_state(self):
        if not self._ready:
            raise RuntimeError("The desktop engine is still initializing.")
        with self._lock:
            settings = dict(self._state["settings"])
            settings.setdefault("folder", str(DEFAULT_FOLDER))
            settings.setdefault("quality", "best")
            settings.setdefault("format", "mp4")
            settings.setdefault("auto_start", False)
            settings.setdefault("keep_metadata", True)
            settings.setdefault("youtube_cookies_enabled", False)
            settings.setdefault("youtube_cookies_browser", "none")
            return {"settings": settings, "jobs": list(self._state["jobs"]), "history": list(self._state["history"])}

    def analyze(self, raw_url):
        host = "unknown"
        cookie_browser = None
        try:
            url = valid_url(raw_url)
            host = urlparse(url).hostname or "unknown"
            log_stage("youtube_metadata_analysis", "started", f"host={host}")
            ensure_writable_temp_dir()
            options = youtube_analysis_options(url)
            cookie_browser = self._cookie_browser(url)
            log_youtube_request("youtube_metadata_analysis", url, cookie_browser)
            def extract_info_with_browser(options_for_attempt, browser_for_attempt):
                with browser_cookie_diagnostics(browser_for_attempt, host) as profile_path:
                    attempt_options = dict(options_for_attempt)
                    if browser_for_attempt:
                        attempt_options["cookiesfrombrowser"] = (browser_for_attempt, profile_path, None, None)
                    with DiagnosticYoutubeDL(attempt_options) as ydl:
                        if browser_for_attempt:
                            load_cookie_jar_for_diagnostics(ydl, browser_for_attempt)
                        return ydl.extract_info(url, download=False)
            info = run_with_cookie_lock_fallback(
                extract_info_with_browser, options, cookie_browser, "youtube_metadata_analysis")
            log_youtube_clients_used("youtube_metadata_analysis", info)
            if not info:
                raise ValueError("The site returned no video information.")
            formats = [clean_format(f) for f in info.get("formats", []) if f.get("format_id")]
            formats = [f for f in formats if f["has_video"] or f["has_audio"]]
            if youtube_host(url) and not formats:
                raise ValueError("YouTube returned no downloadable formats for this video.")
            heights = sorted({f["height"] for f in formats if f["height"]}, reverse=True)
            sizes = [f["filesize"] for f in formats if isinstance(f["filesize"], (int, float)) and f["filesize"] > 0]
            log_stage("youtube_metadata_analysis", "success", f"host={host} extractor={info.get('extractor_key') or info.get('extractor') or 'unknown'} format_count={len(formats)}")
            return {"ok": True, "video": {
                "id": str(info.get("id") or ""), "title": str(info.get("title") or "Untitled video"),
                "thumbnail": str(info.get("thumbnail") or ""), "duration": info.get("duration"),
                "duration_text": self._duration(info.get("duration")), "extractor": str(info.get("extractor_key") or info.get("extractor") or "Video"),
                "uploader": str(info.get("uploader") or info.get("channel") or ""), "view_count": info.get("view_count"),
                "formats": formats, "heights": heights, "estimated_size": max(sizes) if sizes else None,
                "estimated_size_text": self._size(max(sizes)) if sizes else "Unavailable",
            }}
        except Exception as exc:
            status = "authentication_required" if re.search(r"sign in|not a bot|confirm you.?re not", str(exc), re.IGNORECASE) else "failed"
            log_exception("youtube_metadata_analysis", exc)
            log_stage("youtube_metadata_analysis", status, f"host={host} browser={cookie_browser or 'none'} error_type={type(exc).__name__}")
            return {"ok": False, "error": self._message(exc)}

    @staticmethod
    def _duration(seconds):
        if not isinstance(seconds, (int, float)):
            return "Unavailable"
        seconds = int(seconds)
        return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}" if seconds >= 3600 else f"{seconds // 60:02}:{seconds % 60:02}"

    @staticmethod
    def _size(value):
        units = ["B", "KB", "MB", "GB"]
        value = float(value)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}"
            value /= 1024

    @staticmethod
    def _message(exc):
        message = str(exc).strip()
        lowered = message.lower()
        if "could not copy chrome cookie database" in lowered:
            return ("Chrome is locking its cookie database, so VidoSwift could not read the signed-in session. "
                    "Completely exit Chrome, including background processes, then retry in VidoSwift. "
                    "If Chrome keeps running in the background, turn off Chrome's background-app setting before exiting. "
                    "You can also choose another browser in Settings where you are signed in. "
                    "VidoSwift reads browser cookies locally only after your consent; it does not export or log cookie values.")
        if "failed to decrypt with dpapi" in lowered or "chrome cookies could not be read" in lowered:
            return ("Windows could not decrypt cookies from this Chrome profile, and the public-video retry did not work. "
                    "This Chrome profile's cookie encryption is incompatible with the bundled cookie reader. "
                    "For signed-in YouTube videos, choose Firefox in Settings if you are signed in there. "
                    "For public videos, turn off browser-cookie access and retry. VidoSwift does not change Chrome's security settings.")
        if "page needs to be reloaded" in lowered:
            return ("YouTube rejected this request because of a compatibility issue in the bundled download engine. "
                    f"VidoSwift is using yt-dlp {YT_DLP_VERSION}. Install a newer VidoSwift build and retry. "
                    "For public videos, turning browser-cookie access off in Settings may also help.")
        if "no downloadable formats" in lowered or "requested format is not available" in lowered:
            return ("YouTube returned no usable formats for this video. VidoSwift tried its standard YouTube players "
                    "and the embedded-player fallback. The detailed, cookie-safe client/challenge results are in "
                    "%APPDATA%\\VidoSwift\\startup.log. If this is a public video, turning browser-cookie access off "
                    "in Settings may help.")
        if "sign in to confirm" in lowered or "not a bot" in lowered or "confirm you're not" in lowered:
            return ("YouTube requires a signed-in browser session. VidoSwift cannot bypass this check. "
                    "To try your own session, open Settings → YouTube browser cookies, explicitly enable access, "
                    "choose the browser where you are signed in, then retry. yt-dlp reads the selected browser's cookie store locally; "
                    "only YouTube-matching cookies are used for YouTube URLs. VidoSwift does not export, save, or log cookie values. "
                    "You can also try again later or use another public video URL.")
        if "cookie" in lowered and any(term in lowered for term in ("could not", "failed", "unable", "decrypt", "database is locked", "profile", "permission denied")):
            return ("VidoSwift could not read the selected browser's cookie database. Completely exit that browser, "
                    "including background processes, then retry. If it still fails, confirm that you are signed in "
                    "to that browser's default profile or choose another signed-in browser in Settings. "
                    "VidoSwift reads cookies locally only after your consent and never exports, saves, or logs their values.")
        message = re.sub(r"(?i)\b(cookies?|set-cookie|authorization|password|access[_-]?token|refresh[_-]?token)\b\s*[:=].*", r"\1=[redacted]", message)
        return message[:500] or "The request could not be completed."

    def _cookie_browser(self, url):
        settings = self._state.get("settings", {})
        browser = str(settings.get("youtube_cookies_browser", "none")).lower()
        if youtube_host(url) and settings.get("youtube_cookies_enabled") is True and browser in SUPPORTED_COOKIE_BROWSERS:
            return browser
        return None

    def choose_folder(self):
        if not self._window:
            return None
        result = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=self._folder())
        if result:
            return self.set_folder(result[0])
        return None

    def set_folder(self, raw_path):
        path = Path(str(raw_path)).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise ValueError("Choose an existing folder.")
        with tempfile.NamedTemporaryFile(dir=path, prefix=".vidoswift-write-check-", delete=True):
            pass
        with self._lock:
            self._state["settings"]["folder"] = str(path)
            self._save()
        return str(path)

    def save_settings(self, values):
        if not self._ready:
            raise RuntimeError("The desktop engine is still initializing.")
        if not isinstance(values, dict):
            raise ValueError("Invalid settings.")
        with self._lock:
            settings = self._state["settings"]
            quality = str(values.get("quality", settings.get("quality", "best"))).lower()
            fmt = str(values.get("format", settings.get("format", "mp4"))).lower()
            if quality not in ALLOWED_QUALITIES or fmt not in ALLOWED_FORMATS:
                raise ValueError("Unsupported quality or format selection.")
            browser = str(values.get("youtube_cookies_browser", settings.get("youtube_cookies_browser", "none"))).lower()
            cookies_enabled = bool(values.get("youtube_cookies_enabled", settings.get("youtube_cookies_enabled", False)))
            if browser != "none" and browser not in SUPPORTED_COOKIE_BROWSERS:
                raise ValueError("Choose a supported browser for optional YouTube cookies.")
            if cookies_enabled and browser == "none":
                raise ValueError("Choose a browser before enabling optional YouTube cookies.")
            if quality != "audio" and fmt == "mp3":
                fmt = "mp4"
            settings.update({"quality": quality, "format": fmt,
                "auto_start": bool(values.get("auto_start", settings.get("auto_start", False))),
                "keep_metadata": bool(values.get("keep_metadata", settings.get("keep_metadata", True))),
                "youtube_cookies_enabled": cookies_enabled, "youtube_cookies_browser": browser})
            self._save()
        return self.get_state()["settings"]

    def start_download(self, request):
        if not self._ready:
            raise RuntimeError("The desktop engine is still initializing.")
        if not isinstance(request, dict):
            raise ValueError("Invalid download request.")
        quality = str(request.get("quality", "best")).lower()
        fmt = str(request.get("format", "mp4")).lower()
        if quality not in ALLOWED_QUALITIES or fmt not in ALLOWED_FORMATS:
            raise ValueError("Choose a supported quality and format.")
        url = valid_url(request.get("url", ""))
        if quality == "audio":
            fmt = "mp3"
        elif fmt == "mp3":
            raise ValueError("MP3 output requires the Audio only quality selection.")
        folder = self._folder()
        job = {"id": uuid.uuid4().hex, "url": url, "title": str(request.get("title") or url),
            "quality": quality, "format": fmt, "folder": folder, "status": "Queued", "progress": 0,
            "speed": "", "eta": "", "filepath": "", "error": "", "created": time.time()}
        with self._lock:
            self._state["jobs"].insert(0, job)
            self._save()
        self._executor.submit(self._download, job["id"])
        return {"ok": True, "id": job["id"]}

    def _download(self, job_id):
        with self._lock:
            job = next((item for item in self._state["jobs"] if item["id"] == job_id), None)
            if not job:
                return
            job["status"] = "Downloading"
            request = dict(job)
            self._save()
        before = set(Path(request["folder"]).glob("*"))
        limit = {"1080p": 1080, "720p": 720, "480p": 480}.get(request["quality"])
        ext = request["format"]
        if ext == "mp3":
            selector = "bestaudio/best"
            postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
            merge_ext = None
        else:
            cap = f"[height<={limit}]" if limit else ""
            if ext in {"mp4", "webm"}:
                selector = f"bestvideo{cap}[ext={ext}]+bestaudio[ext={'m4a' if ext == 'mp4' else 'webm'}]/best{cap}[ext={ext}]/best{cap}"
            else:
                selector = f"bestvideo{cap}+bestaudio/best{cap}"
            postprocessors = []
            merge_ext = "mkv" if ext == "mkv" else ext
        def hook(data):
            with self._lock:
                if job_id in self._cancelled:
                    raise DownloadCancelled("Cancelled by user")
            updates = {}
            if data.get("status") == "downloading":
                try:
                    updates["progress"] = max(0, min(99, float(data.get("_percent", 0))))
                except (TypeError, ValueError):
                    pass
                updates["speed"] = str(data.get("_speed_str") or "").strip()
                updates["eta"] = str(data.get("_eta_str") or "").strip()
                updates["status"] = "Downloading"
            elif data.get("status") == "finished":
                updates.update({"progress": 99, "status": "Processing"})
            if data.get("filename"):
                updates["filepath"] = str(data["filename"])
            self._update_job(job_id, updates)
        options = {**ytdlp_options(request["url"], 30), "format": selector,
            "outtmpl": os.path.join(request["folder"], f"[{job_id[:8]}] %(title).180B [%(id)s].%(ext)s"),
            "progress_hooks": [hook], "postprocessors": postprocessors,
            "ignoreerrors": False, "windowsfilenames": True}
        cookie_browser = self._cookie_browser(request["url"])
        log_youtube_request("youtube_download", request["url"], cookie_browser)
        if cookie_browser:
            options["cookiesfrombrowser"] = (cookie_browser, None, None, None)
        ffmpeg = BASE_DIR / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if ffmpeg.is_file():
            options["ffmpeg_location"] = str(ffmpeg)
        elif shutil.which("ffmpeg"):
            options["ffmpeg_location"] = shutil.which("ffmpeg")
        if merge_ext:
            options["merge_output_format"] = merge_ext
        if self._state["settings"].get("keep_metadata", True):
            options["writeinfojson"] = True
        try:
            ensure_writable_temp_dir()
            downloader = DiagnosticYoutubeDL if youtube_host(request["url"]) else YoutubeDL
            def download_with_options(options_for_attempt, _browser_for_attempt):
                with downloader(options_for_attempt) as ydl:
                    return ydl.download([request["url"]])
            result = run_with_cookie_lock_fallback(
                download_with_options, options, cookie_browser, "youtube_download")
            if result != 0:
                raise RuntimeError("yt-dlp reported that the download did not complete.")
            with self._lock:
                if job_id in self._cancelled:
                    raise DownloadCancelled("Cancelled by user")
            candidates = [p for p in Path(request["folder"]).glob("*") if p.is_file() and p not in before and p.name.startswith(f"[{job_id[:8]}]") and not p.name.endswith((".part", ".ytdl", ".info.json"))]
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            if not candidates:
                raise RuntimeError("The downloader finished without creating a verifiable output file.")
            output = candidates[0]
            if output.stat().st_size <= 0:
                raise RuntimeError("The output file is empty.")
            self._update_job(job_id, {"status": "Completed", "progress": 100, "filepath": str(output), "speed": "", "eta": ""})
            self._history_from_job(job_id, "Completed", str(output), "")
        except DownloadCancelled as exc:
            self._update_job(job_id, {"status": "Cancelled", "error": self._message(exc), "speed": "", "eta": ""})
            self._history_from_job(job_id, "Cancelled", "", self._message(exc))
        except Exception as exc:
            message = self._message(exc)
            self._update_job(job_id, {"status": "Failed", "error": message, "speed": "", "eta": ""})
            self._history_from_job(job_id, "Failed", "", message)

    def _update_job(self, job_id, updates):
        with self._lock:
            for job in self._state["jobs"]:
                if job["id"] == job_id:
                    job.update(updates)
                    break
            self._save()

    def _history_from_job(self, job_id, status, filepath, error):
        with self._lock:
            job = next((item for item in self._state["jobs"] if item["id"] == job_id), None)
            if not job:
                return
            entry = {**job, "status": status, "filepath": filepath, "error": error, "finished": time.time()}
            self._state["history"] = [item for item in self._state["history"] if item["id"] != job_id]
            self._state["history"].insert(0, entry)
            self._state["history"] = self._state["history"][:500]
            self._save()

    def cancel(self, job_id):
        with self._lock:
            job = next((item for item in self._state["jobs"] if item["id"] == job_id), None)
            if not job or job["status"] not in {"Queued", "Downloading", "Processing"}:
                return False
            self._cancelled.add(job_id)
            if job["status"] == "Queued":
                job["status"] = "Cancelled"
                self._history_from_job(job_id, "Cancelled", "", "Cancelled by user")
            self._save()
        return True

    def retry(self, job_id):
        with self._lock:
            entry = next((item for item in self._state["history"] if item["id"] == job_id), None)
            if not entry or entry["status"] not in {"Failed", "Cancelled"}:
                return {"ok": False, "error": "This item cannot be retried."}
            request = dict(entry)
        return self.start_download(request)

    def clear_history(self):
        with self._lock:
            self._state["history"] = []
            self._save()
        return True

    def open_file(self, filepath):
        path = Path(filepath)
        if not path.is_file():
            raise ValueError("The downloaded file is no longer available.")
        os.startfile(str(path))
        return True

    def open_folder(self, filepath=None):
        path = Path(filepath).parent if filepath else Path(self._folder())
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))
        return True

    def get_clipboard_text(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        except tk.TclError:
            return ""
        finally:
            root.destroy()

    def _close(self):
        with self._lock:
            self._cancelled.update(job["id"] for job in self._state["jobs"] if job["status"] in {"Queued", "Downloading", "Processing"})
        self._executor.shutdown(wait=False, cancel_futures=True)
        log_stage("api_close", "complete")


def resource_path(*parts):
    base = BASE_DIR
    return base.joinpath(*parts)


def load_ui_document(index_path: Path) -> str:
    """Inline the built UI so the desktop app needs no loopback asset server."""
    asset_root = index_path.parent.resolve()
    document = index_path.read_text(encoding="utf-8")
    icon_path = asset_root / "VidoSwift.ico"
    icon_data_url = None
    if icon_path.is_file():
        icon_data = base64.b64encode(icon_path.read_bytes()).decode("ascii")
        icon_data_url = f"data:image/x-icon;base64,{icon_data}"

    def inline_asset(match, tag: str, attribute: str):
        source = re.search(rf'\b{attribute}=["\']([^"\']+)["\']', match.group(0), re.IGNORECASE).group(1)
        if source.startswith(("http://", "https://", "data:")):
            return match.group(0)
        candidate = (asset_root / source.removeprefix("./")).resolve()
        if not candidate.is_relative_to(asset_root) or not candidate.is_file():
            raise FileNotFoundError(f"Built UI asset is missing or outside its folder: {source}")
        content = candidate.read_text(encoding="utf-8")
        if tag == "script":
            if icon_data_url:
                # The app icon is large. Repeating its data URL in each UI
                # image plus the favicon can exceed WebView2's
                # NavigateToString size limit and prevent the bridge from
                # ever becoming ready. Keep one shared data URL in the page.
                content = content.replace("`./VidoSwift.ico`", "window.__vidoswiftIcon")
            content = content.replace("</script", "<\\/script")
            return f"<script type=\"module\">{content}</script>"
        content = content.replace("</style", "<\\/style")
        return f"<style>{content}</style>"

    # Inline the stylesheet, JavaScript bundle, and favicon because WebView2
    # receives a complete in-memory document.
    stylesheet = re.compile(r'<link\b(?=[^>]*\brel=["\']stylesheet["\'])[^>]*>', re.IGNORECASE)
    scripts = re.compile(r'<script\b(?=[^>]*\bsrc=["\'][^"\']+["\'])[^>]*>\s*</script>', re.IGNORECASE)
    document = scripts.sub(lambda match: inline_asset(match, "script", "src"), document)
    styles = re.compile(r'<link\b(?=[^>]*\brel=["\']stylesheet["\'])(?=[^>]*\bhref=["\'][^"\']+["\'])[^>]*>', re.IGNORECASE)
    document = styles.sub(lambda match: inline_asset(match, "link", "href"), document)
    if icon_data_url:
        favicon = '<link rel="icon" type="image/x-icon" href="#vidoswift-icon">'
        icon_link = re.compile(r'<link\b(?=[^>]*\brel=["\']icon["\'])[^>]*>', re.IGNORECASE)
        if icon_link.search(document):
            document = icon_link.sub(favicon, document)
        else:
            document = re.sub(r'</head>', favicon + "</head>", document, count=1, flags=re.IGNORECASE)
        icon_script = f'<script>window.__vidoswiftIcon="{icon_data_url}";document.querySelector("link[rel=icon]").href=window.__vidoswiftIcon;</script>'
        document = re.sub(r'</head>', icon_script + "</head>", document, count=1, flags=re.IGNORECASE)
    else:
        document = re.sub(r'<link\b(?=[^>]*\brel=["\']icon["\'])[^>]*>', "", document, flags=re.IGNORECASE)
    if re.search(r'<script\b(?=[^>]*\bsrc=)|<link\b(?=[^>]*\brel=["\']stylesheet["\'])', document, re.IGNORECASE):
        raise RuntimeError("Built UI still contains an external script or stylesheet.")
    return document


def main():
    configure_diagnostics()
    log_stage("process_start", "started", f"frozen={bool(getattr(sys, 'frozen', False))} pid={os.getpid()}")
    if not acquire_single_instance():
        return
    ui = resource_path("ui", "dist", "index.html")
    if not ui.is_file():
        log_stage("ui_resource", "failed", "FileNotFoundError")
        raise FileNotFoundError(f"Built VidoSwift UI not found: {ui}. Run the build script first.")
    log_stage("ui_resource", "found")
    ui_document = load_ui_document(ui)
    log_stage("ui_document", "inlined")
    api = None
    stage = "api_init"
    try:
        api = VidoSwiftApi()
        stage = "window_create"
        window = webview.create_window(APP_TITLE, html=ui_document, js_api=api, width=1360, height=900,
            min_size=(980, 680), background_color="#09090b", frameless=True, easy_drag=False, shadow=True)
        api._window = window
        log_stage("window_create", "ready")
        def cancel_active_downloads():
            with api._lock:
                api._cancelled.update(job["id"] for job in api._state["jobs"] if job["status"] in {"Queued", "Downloading", "Processing"})
        window.events.closing += cancel_active_downloads
        def diagnose_webview_loaded():
            try:
                status = window.evaluate_js("JSON.stringify({url: location.protocol + '//' + location.host + location.pathname, title: document.title, ready: document.readyState, root: !!document.getElementById('root'), rendered: !!document.getElementById('root')?.childElementCount, scripts: Array.from(document.scripts).map(s => s.src ? new URL(s.src).pathname : 'inline'), api: !!window.pywebview?.api, health: typeof window.pywebview?.api?.health, readyEvent: !!window.__vidoswiftPywebviewReady})")
                log_stage("webview_dom", "loaded", status)
            except Exception as exc:
                log_stage("webview_dom", "diagnostic_failed", type(exc).__name__)
        window.events.loaded += diagnose_webview_loaded
        stage = "webview_start"
        log_stage(stage, "started")
        # pywebview's Windows backend uses TemporaryDirectory().name in private
        # mode when no storage path is supplied. That temporary directory is
        # removed immediately, so WebView2 cannot create its user data folder.
        app_data = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
        webview_data = app_data / "VidoSwift" / "WebView2Sessions" / f"session-{os.getpid()}"
        webview_data.mkdir(parents=True, exist_ok=True)
        log_stage("webview_data", "ready", f"private_session pid={os.getpid()}")
        # Isolate each launch so a closing/abnormal prior WebView2 process can
        # never hold the profile needed by the next VidoSwift startup.
        webview.start(debug=False, http_server=False, private_mode=False,
            storage_path=str(webview_data))
        log_stage(stage, "closed")
    except BaseException as exc:
        log_stage(stage, "failed", f"{type(exc).__name__}: {exc}")
        log_stage("process_exit", "failed", "exit_code=1")
        raise
    finally:
        if api:
            api._close()
        release_single_instance()


if __name__ == "__main__":
    main()


