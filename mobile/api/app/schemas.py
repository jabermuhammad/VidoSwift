from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)


class VideoFormat(BaseModel):
    format_id: str
    ext: str
    height: int | None
    resolution: str
    note: str
    filesize: int | float | None
    has_video: bool
    has_audio: bool


class VideoInfo(BaseModel):
    id: str
    title: str
    thumbnail: str
    duration: int | float | None
    duration_text: str
    extractor: str
    uploader: str
    view_count: int | None
    formats: list[VideoFormat]
    heights: list[int]
    estimated_size: int | float | None
    estimated_size_text: str


class AnalyzeResponse(BaseModel):
    ok: Literal[True] = True
    video: VideoInfo


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    ok: Literal[True] = True
    status: Literal["ready"] = "ready"
    service: str = "vidoswift-mobile-api"
    yt_dlp_version: str
    yt_dlp_channel: str
    youtube_js_runtime_available: bool
