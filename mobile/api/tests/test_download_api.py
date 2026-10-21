from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.services.downloader import DownloadError


client = TestClient(main.app)
VIDEO_URL = "https://youtu.be/example"


def test_download_route_is_documented_and_has_a_probe(monkeypatch):
    monkeypatch.setattr(main, "validate_public_video_url", lambda url: url)
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths["/v1/download"]) == {"get"}
    response = client.head("/v1/download", params={"url": VIDEO_URL, "quality": "720p"})
    assert response.status_code == 204


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
