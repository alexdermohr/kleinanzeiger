"""Unconditionally deny historical unofficial platform transports.

These callable legacy PoC adapters are not official account-scoped API clients.
A browser login, upstream client instance or environment flag is not permission.
Synthetic unit tests may patch this function within their own process only.
"""
from __future__ import annotations


class LegacyPlatformAdmissionDenied(RuntimeError):
    """An unofficial mobile, private-HTTP or browser-bot transport is forbidden."""


def require_legacy_platform_authority() -> None:
    raise LegacyPlatformAdmissionDenied(
        "legacy Kleinanzeigen platform automation not authorized; "
        "use mark-api-offline with local files"
    )
