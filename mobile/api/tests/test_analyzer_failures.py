import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

from app import main
from app.schemas import AnalyzeRequest
from app.services.analyzer import AnalysisError, _analysis_failure, analyze_video, youtube_extractor_args
from yt_dlp.utils import DownloadError as YtDlpDownloadError, ExtractorError


def wrapped_ytdlp_http_error(status: int, message: str) -> YtDlpDownloadError:
    response_error = HTTPError("https://www.youtube.com/youtubei/v1/player", status, message, {}, None)
    extractor_error = ExtractorError("Failed to extract any player response", cause=response_error)
    return YtDlpDownloadError(
        str(extractor_error),
        exc_info=(type(extractor_error), extractor_error, None),
    )


class AnalysisFailureTests(unittest.TestCase):
    def test_public_video_bot_check_is_not_labeled_as_login_required(self):
        failure = _analysis_failure(RuntimeError("Sign in to confirm you're not a bot"))
        self.assertEqual(failure.code, "youtube_request_blocked")
        self.assertEqual(failure.status_code, 503)
        self.assertIn("does not prove the video needs sign-in", failure.message)

    def test_format_403_does_not_claim_the_video_is_private(self):
        failure = _analysis_failure(wrapped_ytdlp_http_error(403, "Forbidden"))
        self.assertEqual(failure.code, "youtube_request_blocked")
        self.assertIn("does not necessarily mean the video requires sign-in", failure.message)

    def test_rate_limit_is_reported_separately(self):
        failure = _analysis_failure(wrapped_ytdlp_http_error(429, "Too Many Requests"))
        self.assertEqual(failure.code, "source_rate_limited")
        self.assertEqual(failure.status_code, 429)

    def test_missing_javascript_runtime_is_actionable(self):
        failure = _analysis_failure(RuntimeError("No supported JavaScript runtime could be found"))
        self.assertEqual(failure.code, "javascript_runtime_required")
        self.assertIn("Node.js 22 or newer", failure.message)

    def test_dns_failure_is_reported_as_backend_network_issue(self):
        failure = _analysis_failure(RuntimeError("Failed to resolve www.youtube.com"))
        self.assertEqual(failure.code, "source_network_error")
        self.assertIn("DNS", failure.message)

    def test_explicit_private_video_remains_an_authentication_error(self):
        failure = _analysis_failure(RuntimeError("This is a private video"))
        self.assertEqual(failure.code, "authentication_required")
        self.assertEqual(failure.status_code, 422)

    def test_unavailable_video_is_not_reported_as_an_unknown_server_error(self):
        failure = _analysis_failure(RuntimeError("Video unavailable"))
        self.assertEqual(failure.code, "video_unavailable")
        self.assertEqual(failure.status_code, 422)

    def test_consent_verification_is_reported_without_raw_details(self):
        failure = _analysis_failure(RuntimeError("Consent cookie required"))
        self.assertEqual(failure.code, "youtube_consent_required")
        self.assertNotIn("cookie", failure.message.lower())

    def test_missing_player_response_is_diagnosed_without_claiming_ip_block(self):
        failure = _analysis_failure(RuntimeError("Failed to extract any player response"))
        self.assertEqual(failure.code, "youtube_player_response_missing")
        self.assertEqual(failure.status_code, 503)
        self.assertIn("does not by itself", failure.message)

    def test_missing_po_token_is_separate_from_generic_forbidden(self):
        failure = _analysis_failure(RuntimeError("PO Token required for this client"))
        self.assertEqual(failure.code, "po_token_required")

    def test_po_token_mention_alone_is_not_classified_as_a_requirement(self):
        failure = _analysis_failure(RuntimeError("The extractor supports PO Token providers."))
        self.assertEqual(failure.code, "analysis_failed")

    def test_current_yt_dlp_uses_library_default_clients_without_forced_embedded_request(self):
        self.assertEqual(youtube_extractor_args("2026.10.10"), {"youtube": {"player_client": ["default"]}})
        self.assertEqual(
            youtube_extractor_args("2026.09.01"),
            {"youtube": {"player_client": ["default", "-tv_downgraded"]}},
        )

    @patch("app.services.analyzer.validate_public_video_url", return_value="https://www.youtube.com/watch?v=abc123")
    @patch("app.services.analyzer.YoutubeDL")
    def test_successful_analysis_uses_metadata_only(self, youtube_dl, _validate):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = {
            "id": "abc123", "title": "Public video", "duration": 90,
            "formats": [{"format_id": "18", "ext": "mp4", "height": 360,
                         "vcodec": "avc1", "acodec": "mp4a"}],
        }
        youtube_dl.return_value = ydl
        result = analyze_video("https://www.youtube.com/watch?v=abc123")
        self.assertEqual(result["video"]["title"], "Public video")
        self.assertEqual(result["video"]["heights"], [360])
        ydl.extract_info.assert_called_once_with("https://www.youtube.com/watch?v=abc123", download=False)

    def test_analyzer_translates_rate_limit_and_logs_only_safe_event_labels(self):
        fake_ydl = MagicMock()
        fake_ydl.__enter__.return_value = fake_ydl
        fake_ydl.extract_info.side_effect = wrapped_ytdlp_http_error(429, "Too Many Requests")
        with patch("app.services.analyzer.validate_public_video_url", return_value="https://youtube.com/watch?v=abc123"), \
             patch("app.services.analyzer.YoutubeDL", return_value=fake_ydl), \
             patch("app.services.analyzer.logger.warning") as warning:
            with self.assertRaises(AnalysisError) as raised:
                analyze_video("https://youtube.com/watch?v=abc123")
        self.assertEqual(raised.exception.code, "source_rate_limited")
        self.assertEqual(warning.call_args.args[1:4], (
            "youtube.com", "source_rate_limited", "DownloadError",
        ))
        self.assertEqual(warning.call_args.args[4:8], ("DownloadError>ExtractorError>HTTPError", "player_response", "429", "none"))
        self.assertNotIn("abc123", str(warning.call_args))

    def test_player_response_failure_logs_exception_types_and_duration_without_raw_details(self):
        fake_ydl = MagicMock()
        fake_ydl.__enter__.return_value = fake_ydl
        fake_ydl.extract_info.side_effect = YtDlpDownloadError(
            "Failed to extract any player response for https://youtube.com/watch?v=private-id"
        )
        with patch("app.services.analyzer.validate_public_video_url", return_value="https://youtube.com/watch?v=private-id"), \
             patch("app.services.analyzer.YoutubeDL", return_value=fake_ydl), \
             patch("app.services.analyzer.logger.warning") as warning:
            with self.assertRaises(AnalysisError):
                analyze_video("https://youtube.com/watch?v=private-id")

        fields = warning.call_args.args[0]
        values = warning.call_args.args[1:]
        self.assertIn("yt_dlp_exception_chain=%s", fields)
        self.assertIn("elapsed_ms=%d", fields)
        self.assertEqual(values[3], "DownloadError")
        self.assertGreaterEqual(values[-1], 0)
        self.assertNotIn("private-id", repr(warning.call_args))

    def test_ytdlp_diagnostic_collector_keeps_only_safe_event_labels(self):
        from app.services.analyzer import _YtDlpDiagnostics

        diagnostics = _YtDlpDiagnostics()
        diagnostics.debug("[debug] Downloading webpage https://youtube.com/watch?v=secret")
        diagnostics.warning("Sign in to confirm you're not a bot")
        self.assertEqual(diagnostics.events, ["webpage_request_attempted", "bot_verification_message"])
        self.assertNotIn("secret", repr(diagnostics.events))

    def test_event_labels_do_not_turn_mentions_into_confirmed_causes(self):
        from app.services.analyzer import _YtDlpDiagnostics

        diagnostics = _YtDlpDiagnostics()
        diagnostics.debug("The extractor supports PO Token providers and JavaScript runtimes.")
        diagnostics.debug("403 and 429 are documented HTTP statuses; the player API may be requested.")

        self.assertIn("po_token_mentioned", diagnostics.events)
        self.assertIn("player_api_attempted", diagnostics.events)
        self.assertNotIn("po_token_requirement_observed", diagnostics.events)
        self.assertNotIn("http_error_text_match_403", diagnostics.events)
        self.assertNotIn("http_error_text_match_429", diagnostics.events)
        self.assertNotIn("js_runtime_unavailable", diagnostics.events)
        failure = _analysis_failure(RuntimeError("Failed to extract any player response"))
        self.assertEqual(failure.code, "youtube_player_response_missing")

    def test_explicit_http_status_and_token_signals_are_distinguished(self):
        from app.services.analyzer import _YtDlpDiagnostics

        diagnostics = _YtDlpDiagnostics()
        diagnostics.error("ERROR: HTTP Error 429: Too Many Requests")
        diagnostics.error("WARNING: A PO Token is required for this client")

        self.assertIn("http_error_text_match_429", diagnostics.events)
        self.assertIn("po_token_requirement_observed", diagnostics.events)
        # Earlier diagnostic lines in a multi-request extraction aren't enough
        # to claim that they caused a later generic player-response failure.
        failure = _analysis_failure(RuntimeError("Failed to extract any player response"))
        self.assertEqual(failure.code, "youtube_player_response_missing")

    def test_http_text_signatures_are_not_treated_as_observed_status_codes(self):
        from app.services.analyzer import _YtDlpDiagnostics, _http_status_from_exception

        diagnostics = _YtDlpDiagnostics()
        diagnostics.error("ERROR: HTTP Error 429: Too Many Requests")
        diagnostics.error("ERROR: HTTP Error 403: Forbidden")
        wrapped_missing_response = YtDlpDownloadError("Failed to extract any player response")

        self.assertIn("http_error_text_match_429", diagnostics.events)
        self.assertIn("http_error_text_match_403", diagnostics.events)
        self.assertIsNone(_http_status_from_exception(wrapped_missing_response))
        self.assertEqual(_analysis_failure(wrapped_missing_response).code, "youtube_player_response_missing")

    def test_wrapped_structured_http_status_is_preserved_through_ytdlp_exceptions(self):
        from app.services.analyzer import _http_status_from_exception

        self.assertEqual(_http_status_from_exception(wrapped_ytdlp_http_error(403, "Forbidden")), 403)
        self.assertEqual(_http_status_from_exception(wrapped_ytdlp_http_error(429, "Too Many Requests")), 429)

    def test_bot_consent_verification_and_unexpected_errors_are_safely_classified(self):
        self.assertEqual(
            _analysis_failure(RuntimeError("Sign in to confirm you're not a bot")).code,
            "youtube_request_blocked",
        )
        unexpected = _analysis_failure(RuntimeError("unexpected parser failure"))
        self.assertEqual(unexpected.code, "analysis_failed")
        self.assertNotIn("parser failure", unexpected.message)

    def test_analyze_route_returns_structured_retryable_upstream_error(self):
        def fail(_url):
            raise _analysis_failure(RuntimeError("Failed to extract any player response"))

        with patch("app.main.analyze_video", side_effect=fail):
            response = main.analyze(AnalyzeRequest(url="https://www.youtube.com/watch?v=abc123"))

        self.assertEqual(response.status_code, 503)
        body = response.body.decode("utf-8")
        self.assertIn('"code":"youtube_player_response_missing"', body)
        self.assertIn('"retryable":true', body)
        self.assertNotIn("DownloadError", body)

    def test_analyze_route_preserves_successful_metadata_response(self):
        payload = {
            "ok": True,
            "video": {
                "id": "abc123", "title": "Public video", "thumbnail": "", "duration": 60,
                "duration_text": "01:00", "extractor": "Youtube", "uploader": "Channel",
                "view_count": 10,
                "formats": [{"format_id": "18", "ext": "mp4", "height": 360,
                             "resolution": "360p", "note": "", "filesize": 1000,
                             "has_video": True, "has_audio": True}],
                "heights": [360], "estimated_size": 1000, "estimated_size_text": "1000.0 B",
            },
        }
        with patch("app.main.analyze_video", return_value=payload):
            response = main.analyze(AnalyzeRequest(url="https://www.youtube.com/watch?v=abc123"))

        self.assertEqual(response, payload)

    def test_download_route_exposes_get_and_head_handlers(self):
        methods = {
            method
            for route in main.app.routes
            if getattr(route, "path", None) == "/v1/download"
            for method in getattr(route, "methods", set())
        }
        self.assertIn("GET", methods)
        self.assertIn("HEAD", methods)

    def test_analyze_route_is_post_and_health_stays_ready(self):
        analyze_methods = {
            method
            for route in main.app.routes
            if getattr(route, "path", None) == "/v1/analyze"
            for method in getattr(route, "methods", set())
        }
        self.assertEqual(analyze_methods, {"POST"})
        health = main.health()
        self.assertTrue(health["ok"])
        self.assertEqual(health["status"], "ready")

    def test_download_handlers_accept_query_contract_without_external_requests(self):
        with TemporaryDirectory(dir=Path.cwd()) as root:
            media = Path(root) / "sample.mp4"
            media.write_bytes(b"test-media")
            with patch("app.main.validate_public_video_url", side_effect=lambda url: url):
                with patch("app.main.download_video", return_value=(media, Path(root), "video/mp4", "sample.mp4")):
                    probe = main.download_probe(url="https://youtu.be/example", quality="720p")
                    download = main.download(url="https://youtu.be/example", quality="720p")

        self.assertEqual(probe.status_code, 204)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.media_type, "video/mp4")


if __name__ == "__main__":
    unittest.main()
