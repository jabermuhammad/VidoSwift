import socket
import json
import tempfile
import unittest
import inspect
from pathlib import Path
from unittest.mock import patch

import app


class UrlValidationTests(unittest.TestCase):
    @patch("app.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    def test_public_https_url_is_accepted(self, _resolve):
        self.assertEqual(app.valid_url(" https://example.com/watch?v=sample "), "https://example.com/watch?v=sample")

    def test_non_http_schemes_are_rejected(self):
        for value in ("file:///C:/private.txt", "javascript:alert(1)", "//example.com/video"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.valid_url(value)


class YoutubeClientCompatibilityTests(unittest.TestCase):
    def test_youtube_video_ids_are_extracted_without_query_strings(self):
        self.assertEqual(app.youtube_video_id("https://youtu.be/cqxWrtlSaL4?si=secret"), "cqxWrtlSaL4")
        self.assertEqual(app.youtube_video_id("https://youtube.com/watch?v=iC-_BgAhzAE&feature=share"), "iC-_BgAhzAE")

    def test_old_yt_dlp_excludes_broken_signed_in_player_client(self):
        self.assertEqual(app.youtube_extractor_args("2026.08.19"), {
            "youtube": {"player_client": ["default", "-tv_downgraded", "web_embedded"]}
        })

    def test_fixed_yt_dlp_keeps_upstream_defaults_and_adds_embed_fallback(self):
        self.assertEqual(app.youtube_extractor_args("2026.10.10.233141.dev0"), {
            "youtube": {"player_client": ["default", "web_embedded"]}
        })

    def test_yt_dlp_compatibility_options_apply_to_youtube_only(self):
        youtube = app.ytdlp_options("https://www.youtube.com/watch?v=aqz-KE-bpKQ", 20)
        self.assertEqual(youtube["extractor_args"], app.youtube_extractor_args())
        self.assertNotIn("extractor_args", app.ytdlp_options("https://example.com/video.mp4", 20))

    def test_metadata_analysis_has_bounded_network_retries(self):
        options = app.youtube_analysis_options("https://youtu.be/aqz-KE-bpKQ")
        self.assertEqual(options["socket_timeout"], 10)
        self.assertEqual(options["retries"], 1)
        self.assertEqual(options["extractor_retries"], 1)
        self.assertTrue(options["skip_download"])
        self.assertTrue(options["ignore_no_formats_error"])

    def test_locked_chrome_cookie_database_retries_without_cookies(self):
        seen = []
        def operation(options, browser):
            seen.append((dict(options), browser))
            if browser:
                raise RuntimeError("ERROR: Could not copy Chrome cookie database")
            return "public metadata"
        result = app.run_with_cookie_lock_fallback(
            operation, {"cookiesfrombrowser": ("chrome", None, None, None)}, "chrome", "youtube_metadata_analysis")
        self.assertEqual(result, "public metadata")
        self.assertEqual(len(seen), 2)
        self.assertIsNone(seen[1][1])
        self.assertNotIn("cookiesfrombrowser", seen[1][0])
        self.assertEqual(seen[1][0]["socket_timeout"], 5)
        self.assertEqual(seen[1][0]["retries"], 0)

    def test_chrome_dpapi_failure_retries_public_video_without_cookies(self):
        seen = []
        def operation(options, browser):
            seen.append((dict(options), browser))
            if browser:
                raise RuntimeError("ERROR: Failed to decrypt with DPAPI")
            return "public metadata"
        result = app.run_with_cookie_lock_fallback(
            operation, {"cookiesfrombrowser": ("chrome", None, None, None)}, "chrome", "youtube_metadata_analysis")
        self.assertEqual(result, "public metadata")
        self.assertEqual(len(seen), 2)
        self.assertIsNone(seen[1][1])
        self.assertNotIn("cookiesfrombrowser", seen[1][0])
        self.assertEqual(seen[1][0]["socket_timeout"], 5)
        self.assertEqual(seen[1][0]["retries"], 0)

    def test_chrome_cookie_failure_with_login_required_has_actionable_error(self):
        def operation(_options, browser):
            if browser:
                raise RuntimeError("ERROR: Failed to decrypt with DPAPI")
            raise RuntimeError("Sign in to confirm you're not a bot")
        with self.assertRaisesRegex(RuntimeError, "Chrome cookies could not be read") as caught:
            app.run_with_cookie_lock_fallback(
                operation, {"cookiesfrombrowser": ("chrome", None, None, None)}, "chrome", "youtube_metadata_analysis")
        message = app.VidoSwiftApi._message(caught.exception)
        self.assertIn("choose Firefox", message)
        self.assertIn("does not change Chrome's security settings", message)

    def test_cookie_fallback_does_not_mask_other_errors(self):
        def operation(_options, _browser):
            raise RuntimeError("YouTube request failed")
        with self.assertRaisesRegex(RuntimeError, "YouTube request failed"):
            app.run_with_cookie_lock_fallback(operation, {}, "chrome", "youtube_metadata_analysis")

    def test_bundled_node_runtime_is_explicitly_configured(self):
        node_path = app.BASE_DIR / "bin" / ("node.exe" if app.os.name == "nt" else "node")
        with patch.object(app, "find_youtube_js_runtime", return_value=str(node_path)):
            options = app.ytdlp_options("https://youtube.com/watch?v=aqz-KE-bpKQ", 20)
        self.assertEqual(options["js_runtimes"], {"node": {"path": str(node_path)}})
        self.assertIsInstance(options["logger"], app.DiagnosticLogger)

    def test_bundled_node_runtime_is_executable_and_supported(self):
        runtime = app.find_youtube_js_runtime()
        self.assertIsNotNone(runtime)
        self.assertTrue(Path(runtime).is_file())

    def test_embedded_client_fallback_is_youtube_only(self):
        youtube = app.ytdlp_options("https://youtu.be/aqz-KE-bpKQ", 20)
        self.assertIn("web_embedded", youtube["extractor_args"]["youtube"]["player_client"])
        self.assertNotIn("extractor_args", app.ytdlp_options("https://example.com/video.mp4", 20))

    def test_actual_ytdlp_client_selection_includes_embed_fallback(self):
        from yt_dlp import YoutubeDL
        url = "https://youtu.be/cqxWrtlSaL4"
        with YoutubeDL(app.ytdlp_options(url, 20)) as ydl:
            ie = ydl.get_info_extractor("Youtube")
            ie.set_downloader(ydl)
            ie.initialize()
            clients = ie._get_requested_clients(url, {}, False)
        self.assertIn("web_embedded", clients)
        self.assertIn("tv_samsung", clients)

    def test_reload_error_message_identifies_engine_compatibility(self):
        message = app.VidoSwiftApi._message(RuntimeError("The page needs to be reloaded"))
        self.assertIn("compatibility issue", message)
        self.assertIn(app.YT_DLP_VERSION, message)

    def test_no_formats_error_suggests_cookie_safe_diagnostics(self):
        message = app.VidoSwiftApi._message(RuntimeError("YouTube returned no downloadable formats for this video."))
        self.assertIn("embedded-player fallback", message)
        self.assertIn("startup.log", message)

    def test_youtube_diagnostics_redact_secrets_from_messages(self):
        logger = app.DiagnosticLogger()
        with patch.object(app, "log_stage") as log:
            logger.warning("PO Token warning https://youtube.test/watch?v=abc&token=do-not-log")
        logged = log.call_args.args[2]
        self.assertIn("[query redacted]", logged)
        self.assertNotIn("do-not-log", logged)


class CookieDiagnosticTests(unittest.TestCase):
    def test_diagnostic_sanitizer_redacts_cookie_values_and_url_queries(self):
        unsafe = "Cookie: SID=private-cookie\nfailed https://youtube.example/watch?v=abc&token=private-token"
        safe = app.sanitize_diagnostic_text(unsafe)
        self.assertNotIn("private-cookie", safe)
        self.assertNotIn("private-token", safe)
        self.assertIn("[REDACTED]", safe)
        self.assertIn("[query redacted]", safe)

    def test_firefox_profile_resolution_records_selected_database(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            profile = Path(temp) / "profile.default"
            profile.mkdir()
            database = profile / "cookies.sqlite"
            database.touch()
            with patch.object(app.yt_dlp_cookies, "_firefox_browser_dirs", return_value=[temp]):
                found_profile, found_database = app.resolve_browser_cookie_profile("firefox")
        self.assertEqual(found_profile, str(profile))
        self.assertEqual(found_database, str(database))

    def test_chromium_profile_resolution_matches_network_cookie_database(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temp:
            profile = Path(temp) / "Profile 2"
            network = profile / "Network"
            network.mkdir(parents=True)
            database = network / "Cookies"
            database.touch()
            config = {"browser_dir": temp, "supports_profiles": True}
            with patch.object(app.yt_dlp_cookies, "_get_chromium_based_browser_settings", return_value=config):
                found_profile, found_database = app.resolve_browser_cookie_profile("chrome")
        self.assertEqual(found_profile, str(profile))
        self.assertEqual(found_database, str(database))

    def test_local_addresses_are_rejected(self):
        for value in ("http://127.0.0.1/video", "http://localhost/video", "http://192.168.1.5/video"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.valid_url(value)

    def test_embedded_credentials_are_rejected(self):
        with self.assertRaises(ValueError):
            app.valid_url("https://user:password@example.com/video")


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.data = Path(self.temp.name)
        self.app_data = app.APP_DATA
        self.state_file = app.STATE_FILE
        app.APP_DATA = self.data
        app.STATE_FILE = self.data / "state.json"

    def create_ready_api(self):
        api = app.VidoSwiftApi()
        self.assertTrue(api._ready_event.wait(3), "background backend initialization timed out")
        self.assertTrue(api.health()["ok"], f"background backend initialization failed: {api._init_error}")
        return api

    def tearDown(self):
        app.APP_DATA = self.app_data
        app.STATE_FILE = self.state_file
        self.temp.cleanup()

    def test_settings_are_validated_and_persisted(self):
        api = self.create_ready_api()
        try:
            settings = api.save_settings({"quality": "720p", "format": "webm", "auto_start": True})
            self.assertEqual(settings["quality"], "720p")
            self.assertEqual(settings["format"], "webm")
            self.assertTrue(settings["auto_start"])
            self.assertTrue(app.STATE_FILE.is_file())
            with self.assertRaises(ValueError):
                api.save_settings({"quality": "unbounded-shell-command"})
            with self.assertRaises(ValueError):
                api.save_settings({"youtube_cookies_enabled": True, "youtube_cookies_browser": "none"})
            with self.assertRaises(ValueError):
                api.save_settings({"youtube_cookies_browser": "unknown"})
        finally:
            api._close()

    def test_youtube_browser_cookies_are_opt_in_and_host_limited(self):
        api = self.create_ready_api()
        try:
            self.assertIsNone(api._cookie_browser("https://www.youtube.com/watch?v=x"))
            api.save_settings({"youtube_cookies_browser": "firefox", "youtube_cookies_enabled": True})
            self.assertEqual(api._cookie_browser("https://youtu.be/x"), "firefox")
            self.assertIsNone(api._cookie_browser("https://example.com/video"))
        finally:
            api._close()

    def test_authentication_error_is_clear_and_does_not_echo_raw_details(self):
        message = app.VidoSwiftApi._message(RuntimeError("Sign in to confirm you're not a bot; cookie=SECRET"))
        self.assertIn("cannot bypass", message.lower())
        self.assertIn("Settings", message)
        self.assertNotIn("SECRET", message)
        self.assertNotIn("SECRET", app.VidoSwiftApi._message(RuntimeError("request failed; Cookie: SID=SECRET; other=SECRET2")))

    def test_chrome_dpapi_error_has_clear_safe_message(self):
        message = app.VidoSwiftApi._message(RuntimeError("ERROR: Failed to decrypt with DPAPI"))
        self.assertIn("could not decrypt cookies", message)
        self.assertIn("choose Firefox", message)
        self.assertNotIn("DPAPI", message)

    def test_invalid_format_is_rejected_before_queueing(self):
        api = self.create_ready_api()
        try:
            with self.assertRaises(ValueError):
                api.start_download({"url": "https://example.com/video", "quality": "best", "format": "exe"})
            self.assertEqual(api.get_state()["jobs"], [])
        finally:
            api._close()

    def test_repeated_backend_start_health_shutdown(self):
        for _ in range(3):
            api = self.create_ready_api()
            self.assertEqual(api.health(), {"ok": True, "status": "ready"})
            api._close()


class BridgeSurfaceTests(unittest.TestCase):
    def test_api_exposes_only_methods_used_by_the_ui(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        saved = (app.APP_DATA, app.STATE_FILE)
        app.APP_DATA = Path(temp.name)
        app.STATE_FILE = app.APP_DATA / "state.json"
        api = app.VidoSwiftApi()
        try:
            self.assertTrue(api._ready_event.wait(3), "backend initialization timed out")
            public = {name for name in dir(api) if not name.startswith("_")}
            expected = {
                "analyze", "cancel", "choose_folder", "clear_history", "get_clipboard_text",
                "get_state", "health", "minimize_window", "open_file", "open_folder", "quit_app", "retry",
                "toggle_maximize_window",
                "save_settings", "set_folder", "start_download",
            }
            self.assertEqual(public, expected)
            self.assertTrue(all(inspect.ismethod(getattr(api, name)) for name in public))
        finally:
            api._close()
            app.APP_DATA, app.STATE_FILE = saved
            temp.cleanup()

    def test_window_control_api_minimizes_and_toggles_maximize(self):
        class FakeWindow:
            def __init__(self):
                self.calls = []
            def minimize(self):
                self.calls.append("minimize")
            def maximize(self):
                self.calls.append("maximize")
            def restore(self):
                self.calls.append("restore")

        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        saved = (app.APP_DATA, app.STATE_FILE)
        app.APP_DATA = Path(temp.name)
        app.STATE_FILE = app.APP_DATA / "state.json"
        api = app.VidoSwiftApi()
        try:
            self.assertTrue(api._ready_event.wait(3), "backend initialization timed out")
            fake_window = FakeWindow()
            api._window = fake_window
            self.assertTrue(api.minimize_window())
            self.assertTrue(api.toggle_maximize_window())
            self.assertFalse(api.toggle_maximize_window())
            self.assertEqual(fake_window.calls, ["minimize", "maximize", "restore"])
        finally:
            api._close()
            app.APP_DATA, app.STATE_FILE = saved
            temp.cleanup()

    def test_pywebview_bridge_walk_does_not_touch_window_gui(self):
        try:
            import webview.util as util
            from webview.window import Window
        except ImportError as exc:
            self.skipTest(f"pywebview unavailable: {exc}")

        class Gui:
            renderer = "edgechromium"
            calls = []

            def get_size(self, _uid):
                self.calls.append("get_size")
                return (1, 1)

            def get_position(self, _uid):
                self.calls.append("get_position")
                return (0, 0)

        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        saved = (app.APP_DATA, app.STATE_FILE)
        app.APP_DATA = Path(temp.name)
        app.STATE_FILE = app.APP_DATA / "state.json"
        api = app.VidoSwiftApi()
        api._ready_event.wait(3)
        window = Window("master", "test", None, html="<p></p>", js_api=api)
        window.gui = Gui()
        window.native = object()
        window.events.shown.set()
        api._window = window
        captured = {}

        def run_js(script):
            if script.startswith("FINISH "):
                captured["functions"] = json.loads(script[len("FINISH "):])

        window.run_js = run_js

        class InlineThread:
            def __init__(self, target):
                self.target = target

            def start(self):
                self.target()

        original = (util.load_js_files, util.Thread)
        util.load_js_files = lambda *_: ("//js", "FINISH %(functions)s")
        util.Thread = InlineThread
        try:
            util.inject_pywebview("edgechromium", window)
        finally:
            util.load_js_files, util.Thread = original
            api._close()
            app.APP_DATA, app.STATE_FILE = saved
            temp.cleanup()
        self.assertEqual({item["func"] for item in captured["functions"]}, {
            "analyze", "cancel", "choose_folder", "clear_history", "get_clipboard_text",
            "get_state", "health", "minimize_window", "open_file", "open_folder", "quit_app", "retry",
            "toggle_maximize_window",
            "save_settings", "set_folder", "start_download",
        })
        self.assertEqual(Gui.calls, [], "bridge injection must not query the native window from a worker thread")
        self.assertTrue(window.events.loaded.is_set())


if __name__ == "__main__":
    unittest.main()
