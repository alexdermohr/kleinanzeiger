"""Safe default entry point: explicit local files, no browser or platform access.

The historical `mark-api-launch` PrivateWeb command remains blocked until
independent platform entitlement is verified. The package module always uses
the existing offline launcher instead.
"""
from __future__ import annotations

from .offline_launcher import main


if __name__ == "__main__":
    raise SystemExit(main())
