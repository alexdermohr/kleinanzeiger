#!/usr/bin/env python3
"""Generate customer-visible release notes from CHANGELOG.md.

Usage: python tools/release_notes.py 1.0.3
       python tools/release_notes.py --current
This script is the only source for public GitHub release notes.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
HEADING_RE = re.compile(
    r"^## \[(?P<version>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\]"
    r"(?: - \d{4}-\d{2}-\d{2})?\s*$", re.MULTILINE
)


class ReleaseNotesError(ValueError):
    """The exact release has no usable customer-facing changelog."""


def current_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def release_notes(version: str, root: Path = ROOT) -> str:
    """Require exactly one meaningful changelog entry for the requested version."""
    if not VERSION_RE.fullmatch(version):
        raise ReleaseNotesError("Invalid stable version")
    source = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if len(source) > 100_000:
        raise ReleaseNotesError("Changelog is unreasonably large")
    matches = list(HEADING_RE.finditer(source))
    positions = [i for i, match in enumerate(matches) if match.group("version") == version]
    if len(positions) != 1:
        raise ReleaseNotesError(f"Exactly one entry for version {version} is required")
    pos = positions[0]
    first = matches[pos].end()
    end = matches[pos + 1].start() if pos + 1 < len(matches) else len(source)
    content = source[first:end].strip()
    if len(content) < 45 or len(content) > 16_000 or not re.search(r"(?m)^- \S", content):
        raise ReleaseNotesError(f"Version {version} needs meaningful bullet-point release notes")
    if re.search(r"(?i)(TODO|TBD|Platzhalter|noch ergänzen)", content):
        raise ReleaseNotesError(f"Version {version} contains placeholders")
    return f"# Kleinanzeiger {version} – Windows 11\n\n{content}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?")
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()
    if args.current == bool(args.version):
        parser.error("Specify one version or --current")
    try:
        print(release_notes(current_version() if args.current else args.version), end="")
    except (ReleaseNotesError, OSError, KeyError, ValueError) as exc:
        print(f"Release-Notizen fehlen: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())