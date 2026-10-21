from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from app.schemas import AnalyzeRequest, AnalyzeResponse, ErrorResponse, HealthResponse
from app.security.url_validation import InvalidVideoURL, validate_public_video_url
from app.services.analyzer import AnalysisError, CHANNEL, __version__ as YT_DLP_VERSION, analyze_video, find_youtube_js_runtime
from app.services.downloader import DownloadError, cleanup_download, download_video


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("vidoswift.mobile_api")

app = FastAPI(
    title="VidoSwift Mobile API",
    description="Video analysis and authorized downloads for the VidoSwift Android client.",
    version="0.2.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url=None,
)

default_origins = "http://localhost:5173,http://127.0.0.1:5173,https://localhost,capacitor://localhost"
allowed_origins = [
    origin.strip()
    for origin in os.environ.get("VIDOSWIFT_CORS_ORIGINS", default_origins).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-VidoSwift-Error", "Content-Disposition"],
)


def error_response(status_code: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "retryable": retryable}},
    )


@app.exception_handler(RequestValidationError)
async def invalid_request_handler(request: Request, _exc: RequestValidationError) -> JSONResponse:
    if request.url.path == "/v1/download":
        response = error_response(422, "invalid_download_request", "Provide a video URL and supported quality in the query string.")
        response.headers["X-VidoSwift-Error"] = "Provide a video URL and supported quality in the query string."
        return response
    return error_response(422, "invalid_request", "Provide a video URL in the request body.")


@app.get("/v1/health", response_model=HealthResponse, tags=["health"])
def health() -> dict:
    return {
        "ok": True,
        "status": "ready",
        "service": "vidoswift-mobile-api",
        "yt_dlp_version": YT_DLP_VERSION,
        "yt_dlp_channel": CHANNEL,
        "youtube_js_runtime_available": find_youtube_js_runtime() is not None,
    }


@app.head("/v1/health", include_in_schema=False, tags=["health"])
def health_head() -> Response:
    return Response(status_code=200)


@app.post(
    "/v1/analyze",
    response_model=AnalyzeResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Invalid URL, platform restriction, or unavailable formats."},
        429: {"model": ErrorResponse, "description": "YouTube is rate-limiting analysis requests."},
        502: {"model": ErrorResponse, "description": "The platform did not return usable metadata."},
        503: {"model": ErrorResponse, "description": "YouTube could not provide a player response or requires an unavailable verification step."},
        504: {"model": ErrorResponse, "description": "Analysis timed out and can be retried."},
    },
    tags=["analysis"],
)
def analyze(request: AnalyzeRequest) -> dict | JSONResponse:
    try:
        return analyze_video(request.url)
    except InvalidVideoURL as exc:
        return error_response(422, "invalid_url", str(exc))
    except AnalysisError as exc:
        return error_response(exc.status_code, exc.code, exc.message, retryable=exc.retryable)
    except Exception as exc:
        logger.warning("unexpected analysis error exception_type=%s", type(exc).__name__)
        return error_response(
            502,
            "analysis_failed",
            "The video platform did not return usable video information.",
            retryable=True,
        )


def download_error_response(status_code: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    response = error_response(status_code, code, message, retryable=retryable)
    response.headers["X-VidoSwift-Error"] = message
    return response


@app.head("/v1/download", include_in_schema=False, response_model=None, tags=["download"])
def download_probe(
    url: str = Query(min_length=1, max_length=2048),
    quality: str = Query(default="720p", max_length=8),
) -> Response | JSONResponse:
    if quality not in {"360p", "480p", "720p", "1080p", "1440p", "2160p", "best"}:
        return download_error_response(400, "invalid_quality", "Choose a supported video quality.")
    try:
        validate_public_video_url(url)
    except InvalidVideoURL as exc:
        return download_error_response(422, "invalid_url", str(exc))
    return Response(status_code=204)


@app.get(
    "/v1/download",
    response_model=None,
    responses={
        400: {"model": ErrorResponse, "description": "Unsupported quality option."},
        422: {"model": ErrorResponse, "description": "The source rejected or could not provide the requested download."},
        429: {"model": ErrorResponse, "description": "Download capacity is busy or the source is rate-limiting requests."},
        503: {"model": ErrorResponse, "description": "The server is not configured for this media format."},
    },
    tags=["download"],
)
def download(
    url: str = Query(min_length=1, max_length=2048),
    quality: str = Query(default="720p", max_length=8),
) -> FileResponse | Response | JSONResponse:
    if quality not in {"360p", "480p", "720p", "1080p", "1440p", "2160p", "best"}:
        return download_error_response(400, "invalid_quality", "Choose a supported video quality.")
    try:
        path, temp_dir, media_type, filename = download_video(url, quality)
        return FileResponse(
            path,
            media_type=media_type,
            filename=filename,
            background=BackgroundTask(cleanup_download, temp_dir),
            headers={"X-VidoSwift-Download": "ready"},
        )
    except InvalidVideoURL as exc:
        return download_error_response(422, "invalid_url", str(exc))
    except DownloadError as exc:
        return download_error_response(exc.status_code, exc.code, exc.message, retryable=exc.retryable)
    except Exception as exc:
        logger.warning("unexpected download error exception_type=%s", type(exc).__name__)
        return download_error_response(422, "download_failed", "The source rejected the download or the selected format is unavailable.", retryable=True)
