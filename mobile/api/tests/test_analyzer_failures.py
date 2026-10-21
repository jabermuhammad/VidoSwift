import unittest

from app.services.analyzer import _analysis_failure


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


if __name__ == "__main__":
    unittest.main()
