"""Offline: which YouTube player clients does yt-dlp request for a given config?
No network. Fake cookies only mark the session as 'signed in' for yt-dlp's own check."""
import sys, json
from http.cookiejar import Cookie
import yt_dlp
from yt_dlp import YoutubeDL
from yt_dlp.extractor.youtube import YoutubeIE

def cookie(name):
    return Cookie(0, name, "x", None, False, ".youtube.com", True, True, "/", True, True, None, False, None, None, {})

def requested(authed, args=None):
    opts = {"quiet": True, "logger": type("L", (), {"debug": lambda *a: 0, "warning": lambda *a: 0, "error": lambda *a: 0})()}
    if args: opts["extractor_args"] = {"youtube": args}
    with YoutubeDL(opts) as ydl:
        if authed:
            for n in ("LOGIN_INFO", "SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID"):
                ydl.cookiejar.set_cookie(cookie(n))
        ie = ydl.get_info_extractor("Youtube")
        ie.set_downloader(ydl); ie.initialize()
        return list(ie._get_requested_clients("https://www.youtube.com/watch?v=x", {}, False)), ie.is_authenticated

print("yt-dlp", yt_dlp.version.__version__, yt_dlp.version.CHANNEL)
for label, authed, args in [
    ("no cookies, default config", False, None),
    ("cookies (signed in), default config", True, None),
    ("cookies, player_client=default,-tv_downgraded", True, {"player_client": ["default", "-tv_downgraded"]}),
]:
    clients, auth = requested(authed, args)
    print(f"  {label:50s} authed={auth!s:5} -> {clients}")
