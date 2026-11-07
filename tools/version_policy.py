#!/usr/bin/env python3
"""Check released Kleinanzeiger version consistency and perform explicit SemVer bumps.

Usage:
    python tools/version_policy.py check
    python tools/version_policy.py bump patch|minor|major

pyproject.toml is the canonical product version. Current-version copies required
by the Windows distribution remain guarded until the workflow is fully dynamic.
Stable versions in the Windows update self-test are deliberate fixtures, not
current-release declarations.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
NUM = r"(?P<version>[0-9]+\.[0-9]+\.[0-9]+)"

# Only real distribution references, not historical examples or pinned
# dependency versions. Every pattern must match at least once.
RELEASE_REFERENCES = {
    ".github/workflows/windows.yml": (
        rf"publish-windows-v{NUM}",
        rf"release_tag='v{NUM}'",
        rf"Kleinanzeiger-v{NUM}-(?:Windows\.zip|Windows\.sha256|Release-Manifest\.json|Release-Notes\.md)",
        rf"kleinanzeiger-{NUM}-py3-none-any\.whl",
        rf"version\('kleinanzeiger'\)\s*==\s*'{NUM}'",
        rf"-ExpectedInstalledVersion\s+'{NUM}'",
        rf'"version":"{NUM}"',
        rf"Kleinanzeiger {NUM} – Windows",
    ),
    "scripts/windows-install.ps1": (
        rf"kleinanzeiger-{NUM}-py3-none-any\.whl",
        rf"m\.version\('kleinanzeiger'\)\s*==\s*'{NUM}'",
    ),
    "README.md": (
        rf"\*\*Version {NUM} · Windows",
        rf"/releases/tag/v{NUM}",
    ),
    "docs/windows-installation.md": (
        rf"\*\*Version {NUM} · Windows",
        rf"Kleinanzeiger-v{NUM}-Windows\.zip",
        rf"/releases/tag/v{NUM}",
    ),
    "docs/START-HIER.html": (rf"Kleinanzeiger {NUM} · Windows 11",),
    "docs/Kleinanzeiger-Handbuch.html": (rf"Kleinanzeiger {NUM} · Windows 11",),
    "docs/Kleinanzeiger-Funktionen.html": (rf"Kleinanzeiger {NUM} · Windows 11",),
}


class VersionPolicyError(ValueError):
    """A candidate cannot be safely released."""


def parse_version(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value)
    if match is None:
        raise VersionPolicyError(f"Not a stable SemVer X.Y.Z: {value!r}")
    return tuple(map(int, match.groups()))


def version_string(value: tuple[int, int, int]) -> str:
    return ".".join(str(number) for number in value)


def next_version(old: tuple[int, int, int], kind: str) -> tuple[int, int, int]:
    major, minor, patch = old
    if kind == "patch":
        return (major, minor, patch + 1)
    if kind == "minor":
        return (major, minor + 1, 0)
    if kind == "major":
        return (major + 1, 0, 0)
    raise VersionPolicyError(f"Unknown release kind: {kind}")


def validate_successor(
    candidate: tuple[int, int, int], latest: tuple[int, int, int] | None
) -> None:
    if latest is None or candidate == latest:
        return
    if candidate < latest:
        raise VersionPolicyError(
            f"Version {version_string(candidate)} is older than published {version_string(latest)}"
        )
    allowed = {next_version(latest, kind) for kind in ("patch", "minor", "major")}
    if candidate not in allowed:
        options = ", ".join(sorted(map(version_string, allowed)))
        raise VersionPolicyError(
            f"{version_string(candidate)} skips a release step after "
            f"{version_string(latest)}; expected one of: {options}"
        )


def latest_tag(root: Path) -> tuple[int, int, int] | None:
    output = subprocess.check_output(
        ["git", "tag", "--list", "v*"], cwd=root, text=True
    )
    versions = []
    for line in output.splitlines():
        if line.startswith("v") and SEMVER.fullmatch(line[1:]):
            versions.append(parse_version(line[1:]))
    return max(versions, default=None)


def canonical_version(root: Path) -> tuple[int, int, int]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return parse_version(data["project"]["version"])


def check_references(root: Path, expected: tuple[int, int, int]) -> int:
    target = version_string(expected)
    matched = 0
    for filename, patterns in RELEASE_REFERENCES.items():
        path = root / filename
        content = path.read_text(encoding="utf-8")
        for expression in patterns:
            references = list(re.finditer(expression, content))
            if not references:
                raise VersionPolicyError(f"Missing release reference in {filename}: {expression}")
            for ref in references:
                actual = ref.group("version")
                if actual != target:
                    raise VersionPolicyError(
                        f"{filename} has version {actual}; pyproject.toml requires {target}"
                    )
                matched += 1
    return matched


def check(root: Path = ROOT) -> tuple[int, int, int]:
    current = canonical_version(root)
    reference_count = check_references(root, current)
    previous = latest_tag(root)
    validate_successor(current, previous)
    print(
        f"SemVer OK: {version_string(current)}; "
        f"{reference_count} release references; "
        f"latest tag: {version_string(previous) if previous else 'none'}"
    )
    return current


def bump(root: Path, kind: str) -> tuple[int, int, int]:
    current = check(root)
    previous = latest_tag(root)
    if previous is not None and current != previous:
        raise VersionPolicyError(
            "Current version is not yet released. Do not skip an unpublished version."
        )
    following = next_version(current, kind)
    old_text = version_string(current)
    new_text = version_string(following)
    paths = [root / "pyproject.toml"] + [root / name for name in RELEASE_REFERENCES]
    replacements = {}
    for path in paths:
        old = path.read_text(encoding="utf-8")
        new = old.replace(old_text, new_text)
        if new == old:
            raise VersionPolicyError(f"Expected version missing from {path}")
        replacements[path] = new
    for path, new in replacements.items():
        path.write_text(new, encoding="utf-8")
    check(root)
    print(f"Version bumped {old_text} -> {new_text} ({kind}). Review release notes before merge.")
    return following


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    bump_parser = commands.add_parser("bump")
    bump_parser.add_argument("kind", choices=("patch", "minor", "major"))
    arguments = parser.parse_args()
    try:
        if arguments.command == "check":
            check()
        else:
            bump(ROOT, arguments.kind)
    except (VersionPolicyError, OSError, subprocess.CalledProcessError, KeyError) as exc:
        print(f"SemVer check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
