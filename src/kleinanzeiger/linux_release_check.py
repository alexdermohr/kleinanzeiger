"""Non-blocking KDE release awareness via the official public latest-release redirect.

No account access, installer, marketplace request, or automatic download.
"""
from __future__ import annotations

import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

LATEST_URL = "https://github.com/alexdermohr/kleinanzeiger/releases/latest"
_TAG = re.compile(r"/alexdermohr/kleinanzeiger/releases/tag/v([0-9]+\.[0-9]+\.[0-9]+)\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")


def newer(installed: str, latest: str | None) -> bool:
    """Compare only stable numeric releases, without string-order errors."""
    return (
        isinstance(latest, str)
        and isinstance(installed, str)
        and _VERSION.fullmatch(latest) is not None
        and _VERSION.fullmatch(installed) is not None
        and tuple(map(int, latest.split("."))) > tuple(map(int, installed.split(".")))
    )


def release_url(version: str) -> str:
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ValueError("invalid release version")
    return LATEST_URL.removesuffix("/latest") + "/tag/v" + version


class _OfficialReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https" or parsed.hostname != "github.com"
            or parsed.port is not None or parsed.username or parsed.password
            or _TAG.fullmatch(parsed.path) is None
            or parsed.query or parsed.fragment
        ):
            raise ValueError("release redirect left the official stable tag")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def latest_release() -> str | None:
    """Look up only a stable official tag. All network failures are nonfatal."""
    try:
        context = ssl.create_default_context()
        if not context.get_ca_certs():
            context.load_verify_locations(cafile="/etc/ssl/certs/ca-certificates.crt")
        opener = build_opener(
            ProxyHandler({}), HTTPSHandler(context=context), _OfficialReleaseRedirect()
        )
        with opener.open(
            Request(LATEST_URL, headers={"User-Agent": "Kleinanzeiger/Linux-Release-Check"}),
            timeout=7,
        ) as response:
            if response.status != 200:
                return None
            final = urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != "github.com" or final.port is not None:
            return None
        if final.username or final.password or final.query or final.fragment:
            return None
        match = _TAG.fullmatch(final.path)
        return match[1] if match else None
    except (OSError, HTTPError, URLError, ValueError, OverflowError):
        return None
