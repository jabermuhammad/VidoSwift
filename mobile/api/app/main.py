from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.schemas import AnalyzeRequest, AnalyzeResponse, ErrorResponse, HealthResponse
from app.security.url_validation import InvalidVideoURL
from app.services.analyzer import AnalysisError, CHANNEL, __version__ as YT_DLP_VERSION, analyze_video, find_youtube_js_runtime


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("vidoswift.mobile_api")

app = FastAPI(
    title="VidoSwift Mobile API",
    description="Initial URL analysis API for the VidoSwift Android client.",
    version="0.1.0",
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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


def error_response(status_code: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "retryable": retryable}},
    )


@app.exception_handler(RequestValidationError)
async def invalid_request_handler(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return error_response(422, "invalid_request", "Provide a video URL in the request body.")


@app.api_route(
    "/v1/health",
    methods=["GET", "HEAD"],
    response_model=HealthResponse,
    tags=["health"],
)
def health() -> dict:
    return {
        "ok": True,
        "status": "ready",
        "service": "vidoswift-mobile-api",
        "yt_dlp_version": YT_DLP_VERSION,
        "yt_dlp_channel": CHANNEL,
        "youtube_js_runtime_available": find_youtube_js_runtime() is not None,
    }


@app.post(
    "/v1/analyze",
    response_model=AnalyzeResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Invalid URL, platform restriction, or unavailable formats."},
        502: {"model": ErrorResponse, "description": "The platform did not return usable metadata."},
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
