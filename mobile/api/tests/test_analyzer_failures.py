import unittest
from unittest.mock import MagicMock, patch

from app.services.analyzer import AnalysisError, _analysis_failure, analyze_video


class AnalysisFailureTests(unittest.TestCase):
    def test_public_video_bot_check_is_not_labeled_as_login_required(self):
        failure = _analysis_failure(RuntimeError("Sign in to confirm you're not a bot"))
        self.assertEqual(failure.code, "youtube_request_blocked")
        self.assertEqual(failure.status_code, 503)
        self.assertIn("does not prove the video needs sign-in", failure.message)

    def test_format_403_does_not_claim_the_video_is_private(self):
        failure = _analysis_failure(RuntimeError("HTTP Error 403: Forbidden"))
        self.assertEqual(failure.code, "youtube_request_blocked")
        self.assertIn("does not necessarily mean the video requires sign-in", failure.message)

    def test_rate_limit_is_reported_separately(self):
        failure = _analysis_failure(RuntimeError("HTTP Error 429: Too Many Requests"))
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
        fake_ydl.extract_info.side_effect = RuntimeError("HTTP 429 Too Many Requests")
        with patch("app.services.analyzer.validate_public_video_url", return_value="https://youtube.com/watch?v=abc123"), \
             patch("app.services.analyzer.YoutubeDL", return_value=fake_ydl), \
             patch("app.services.analyzer.logger.warning") as warning:
            with self.assertRaises(AnalysisError) as raised:
                analyze_video("https://youtube.com/watch?v=abc123")
        self.assertEqual(raised.exception.code, "source_rate_limited")
        self.assertEqual(warning.call_args.args[1:], (
            "youtube.com", "source_rate_limited", "RuntimeError", "",
        ))
        self.assertNotIn("abc123", str(warning.call_args))

    def test_ytdlp_diagnostic_collector_keeps_only_safe_event_labels(self):
        from app.services.analyzer import _YtDlpDiagnostics

        diagnostics = _YtDlpDiagnostics()
        diagnostics.debug("[debug] Downloading webpage https://youtube.com/watch?v=secret")
        diagnostics.warning("Sign in to confirm you're not a bot")
        self.assertEqual(diagnostics.events, {"webpage_request", "bot_or_consent_check"})
        self.assertNotIn("secret", repr(diagnostics.events))

    def test_bot_consent_verification_and_unexpected_errors_are_safely_classified(self):
        self.assertEqual(
            _analysis_failure(RuntimeError("Sign in to confirm you're not a bot")).code,
            "youtube_request_blocked",
        )
        unexpected = _analysis_failure(RuntimeError("unexpected parser failure"))
        self.assertEqual(unexpected.code, "analysis_failed")
        self.assertNotIn("parser failure", unexpected.message)


if __name__ == "__main__":
    unittest.main()
