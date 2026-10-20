from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class InvalidVideoURL(ValueError):
    """A URL is malformed, unsafe, or does not resolve to a public host."""


def validate_public_video_url(value: str) -> str:
    """Validate an HTTP(S) URL and reject local/private network destinations.

    This validates the submitted URL before yt-dlp sees it. A deployment must
    also enforce outbound network restrictions because extractors can follow
    redirects and DNS answers may change between validation and connection.
    """
    url = (value or "").strip()
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise InvalidVideoURL("Enter a valid public video URL.") from exc

    if parsed.scheme not in {"http", "https"} or not hostname or "." not in hostname:
        raise InvalidVideoURL("Enter a public video URL beginning with http:// or https://.")
    if parsed.username or parsed.password:
        raise InvalidVideoURL("URLs containing embedded credentials are not supported.")
    if port not in {None, 80, 443}:
        raise InvalidVideoURL("URLs using nonstandard ports are not supported.")

    host = hostname.rstrip(".").lower()
    if not host or "%" in host:
        raise InvalidVideoURL("Enter a valid public video URL.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal", ".lan")):
        raise InvalidVideoURL("Local network addresses are not supported.")

    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii")
            resolved = socket.getaddrinfo(
                ascii_host,
                port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
            addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}
        except (UnicodeError, socket.gaierror, OSError, ValueError) as exc:
            raise InvalidVideoURL("The video host could not be resolved to a public address.") from exc

    if not addresses or any(not address.is_global for address in addresses):
        raise InvalidVideoURL("Local and private network addresses are not supported.")

    return url
