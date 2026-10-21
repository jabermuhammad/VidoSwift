from pathlib import Path
from unittest.mock import MagicMock, patch
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError as YtDlpDownloadError

from fastapi.testclient import TestClient

from app import main
from app.services.downloader import DownloadError, _quality_format_selector, download_video


client = TestClient(main.app)
VIDEO_URL = "https://youtu.be/example"


def test_download_route_is_documented_and_has_a_probe(monkeypatch):
    monkeypatch.setattr(main, "validate_public_video_url", lambda url: url)
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths["/v1/download"]) == {"get"}
    download = MagicMock(side_effect=AssertionError("HEAD must not start a media download"))
    monkeypatch.setattr(main, "download_video", download)
    response = client.head("/v1/download", params={"url": VIDEO_URL, "quality": "720p"})
    assert response.status_code == 204
    assert response.content == b""
    download.assert_not_called()


def test_download_streams_file_and_cleans_temporary_directory(tmp_path: Path, monkeypatch):
    job_dir = tmp_path / "download-job"
    job_dir.mkdir()
    media = job_dir / "video.mp4"
    media.write_bytes(b"sample-video")
    monkeypatch.setattr(main, "validate_public_video_url", lambda url: url)
    monkeypatch.setattr(main, "download_video", lambda url, quality: (media, job_dir, "video/mp4", "video.mp4"))

    response = client.get("/v1/download", params={"url": VIDEO_URL, "quality": "720p"})

    assert response.status_code == 200
    assert response.content == b"sample-video"
    assert response.headers["content-type"] == "video/mp4"
    assert "attachment" in response.headers["content-disposition"]
    assert not job_dir.exists()


def test_invalid_quality_returns_readable_error_header():
    response = client.get("/v1/download", params={"url": VIDEO_URL, "quality": "720"})
    assert response.status_code == 400
    assert response.headers["x-vidoswift-error"] == "Choose a supported video quality."


def test_download_failure_keeps_structured_error_and_header(monkeypatch):
    def fail(_url, _quality):
        raise DownloadError("source_rate_limited", "Wait and retry.", status_code=429, retryable=True)

    monkeypatch.setattr(main, "download_video", fail)
    response = client.get("/v1/download", params={"url": VIDEO_URL, "quality": "720p"})

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "Wait and retry."
    assert response.headers["x-vidoswift-error"] == "Wait and retry."


def test_quality_unavailable_is_a_clear_client_error(monkeypatch):
    def quality_unavailable(_url, _quality):
        raise DownloadError(
            "quality_unavailable",
            "No downloadable audio/video format is available at 480p or lower. Choose another listed quality, or try Best available.",
            status_code=422,
        )

    monkeypatch.setattr(main, "download_video", quality_unavailable)
    response = client.get("/v1/download", params={"url": VIDEO_URL, "quality": "480p"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "quality_unavailable"
    assert "Best available" in response.headers["x-vidoswift-error"]


def test_quality_selectors_preserve_height_cap_and_merge_adaptive_streams():
    assert _quality_format_selector("480p", ffmpeg_available=True) == (
        "bestvideo[height<=480]+bestaudio/best[height<=480]"
    )
    assert _quality_format_selector("720p", ffmpeg_available=False) == (
        "best[height<=720][ext=mp4]/best[height<=720]"
    )
    assert _quality_format_selector("best", ffmpeg_available=True) == "bestvideo*+bestaudio/best"
    assert _quality_format_selector("best", ffmpeg_available=False) == "best[ext=mp4]/best"


def test_yt_dlp_merges_available_480p_video_audio_and_respects_ffmpegless_fallback():
    info = {
        "id": "sample",
        "title": "Sample",
        "formats": [
            {"format_id": "v480", "height": 480, "vcodec": "avc1", "acodec": "none",
             "ext": "mp4", "quality": 5, "url": "https://example.com/v480"},
            {"format_id": "a128", "height": None, "vcodec": "none", "acodec": "mp4a",
             "ext": "m4a", "quality": 1, "url": "https://example.com/a128"},
            {"format_id": "v720", "height": 720, "vcodec": "avc1", "acodec": "none",
             "ext": "mp4", "quality": 7, "url": "https://example.com/v720"},
            {"format_id": "mux360", "height": 360, "vcodec": "avc1", "acodec": "mp4a",
             "ext": "mp4", "quality": 3, "url": "https://example.com/mux360"},
        ],
    }
    with YoutubeDL({"quiet": True}) as ydl:
        merge_selector = ydl.build_format_selector(
            _quality_format_selector("480p", ffmpeg_available=True)
        )
        merged = list(merge_selector(info))
        progressive_selector = ydl.build_format_selector(
            _quality_format_selector("480p", ffmpeg_available=False)
        )
        progressive = list(progressive_selector(info))

    assert [item["format_id"] for item in merged[0]["requested_formats"]] == ["v480", "a128"]
    assert progressive[0]["format_id"] == "mux360"


def test_unavailable_quality_returns_actionable_error_without_raw_ytdlp_text(tmp_path, monkeypatch):
    job_dir = tmp_path / "download-job"
    job_dir.mkdir()
    fake_ydl = MagicMock()
    fake_ydl.__enter__.return_value = fake_ydl
    fake_ydl.download.side_effect = YtDlpDownloadError(
        "Requested format is not available. Use --list-formats to see available formats"
    )

    with patch("app.services.downloader.validate_public_video_url", return_value=VIDEO_URL), \
         patch("app.services.downloader.shutil.which", return_value="ffmpeg"), \
         patch("app.services.downloader.tempfile.mkdtemp", return_value=str(job_dir)), \
         patch("app.services.downloader.YoutubeDL", return_value=fake_ydl):
        try:
            download_video(VIDEO_URL, "480p")
        except DownloadError as exc:
            assert exc.code == "quality_unavailable"
            assert exc.status_code == 422
            assert "480p or lower" in exc.message
            assert "Best available" in exc.message
            assert "Requested format is not available" not in exc.message
        else:
            raise AssertionError("An unavailable requested quality must return a structured error")


def test_bot_verification_is_not_mislabeled_as_video_authentication_required(tmp_path, monkeypatch):
    job_dir = tmp_path / "download-job"
    job_dir.mkdir()
    fake_ydl = MagicMock()
    fake_ydl.__enter__.return_value = fake_ydl
    fake_ydl.download.side_effect = YtDlpDownloadError("Sign in to confirm you're not a bot")

    with patch("app.services.downloader.validate_public_video_url", return_value=VIDEO_URL), \
         patch("app.services.downloader.shutil.which", return_value="ffmpeg"), \
         patch("app.services.downloader.tempfile.mkdtemp", return_value=str(job_dir)), \
         patch("app.services.downloader.YoutubeDL", return_value=fake_ydl):
        try:
            download_video(VIDEO_URL, "480p")
        except DownloadError as exc:
            assert exc.code == "youtube_bot_verification"
            assert exc.status_code == 503
            assert exc.retryable is True
            assert "public videos" in exc.message
        else:
            raise AssertionError("Bot verification must return a distinct retryable error")
