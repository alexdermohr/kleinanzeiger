"""Regression tests for the Windows customer release version policy."""

import tempfile
import unittest
from pathlib import Path

from tools.version_policy import (
    RELEASE_REFERENCES,
    VersionPolicyError,
    bump,
    canonical_version,
    check,
    check_references,
    next_version,
    require_bump_for_customer_changes,
    parse_version,
    validate_successor,
)
from tools.release_notes import release_notes, ReleaseNotesError


def add_customer_note(root: Path, version: tuple[int, int, int]) -> None:
    target = root / "CHANGELOG.md"
    header = "## [" + ".".join(map(str, version)) + "] - 2099-01-01"
    old = target.read_text(encoding="utf-8")
    target.write_text(
        header + "\n\n### Neu\n- Kunden erhalten eine sichtbare Änderungsübersicht für diese Version.\n\n" + old,
        encoding="utf-8",
    )



class VersionPolicyTests(unittest.TestCase):
    def test_semver_has_exactly_three_strict_numbers(self):
        self.assertEqual(parse_version("1.0.1"), (1, 0, 1))
        for invalid in ("v1.0.1", "01.0.1", "1.00.1", "1.0.01", "1.0", "1.0.1-rc.1"):
            with self.subTest(invalid=invalid), self.assertRaises(VersionPolicyError):
                parse_version(invalid)

    def test_next_version_resets_lower_components(self):
        self.assertEqual(next_version((1, 2, 3), "patch"), (1, 2, 4))
        self.assertEqual(next_version((1, 2, 3), "minor"), (1, 3, 0))
        self.assertEqual(next_version((1, 2, 3), "major"), (2, 0, 0))
        with self.assertRaises(VersionPolicyError):
            next_version((1, 2, 3), "feature")

    def test_no_reuse_downgrade_or_skipped_versions(self):
        for allowed in ((1, 0, 1), (1, 0, 2), (1, 1, 0), (2, 0, 0)):
            validate_successor(allowed, (1, 0, 1))
        for invalid in ((0, 1, 49), (1, 0, 3), (1, 2, 0), (2, 1, 0)):
            with self.subTest(invalid=invalid), self.assertRaises(VersionPolicyError):
                validate_successor(invalid, (1, 0, 1))

    def test_current_version_has_customer_release_notes(self):
        root = Path(__file__).resolve().parents[1]
        current = ".".join(map(str, canonical_version(root)))
        self.assertIn("Kleinanzeiger " + current, release_notes(current, root))
        with self.assertRaises(ReleaseNotesError):
            release_notes("99.99.99", root)

    def test_customer_release_references_match_project_version(self):
        root = Path(__file__).resolve().parents[1]
        self.assertGreater(check_references(root, canonical_version(root)), 20)

    def test_mismatched_readme_release_link_is_rejected(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            for filename in (
                "pyproject.toml",
                *RELEASE_REFERENCES,
            ):
                path = copy / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((root / filename).read_bytes())
            readme = copy / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8").replace(
                    "/releases/tag/v" + ".".join(map(str, canonical_version(root))),
                    "/releases/tag/v9.9.9",
                ),
                encoding="utf-8",
            )
            with self.assertRaises(VersionPolicyError):
                check_references(copy, canonical_version(copy))


    def test_bump_updates_packaged_versions_without_manual_edits(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            for filename in ("pyproject.toml", "CHANGELOG.md", *RELEASE_REFERENCES):
                target = copy / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((root / filename).read_bytes())
            import subprocess

            subprocess.run(["git", "init", "-q"], cwd=copy, check=True)
            current = canonical_version(copy)
            expected = next_version(current, "minor")
            self.assertEqual(bump(copy, "minor"), expected)
            self.assertEqual(canonical_version(copy), expected)
            self.assertGreater(check_references(copy, expected), 20)
            with self.assertRaisesRegex(VersionPolicyError, "Missing release notes"):
                check(copy)
            add_customer_note(copy, expected)
            self.assertEqual(check(copy), expected)


    def test_manifest_version_assertion_mismatch_is_rejected(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            for filename in ("pyproject.toml", *RELEASE_REFERENCES):
                target = copy / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((root / filename).read_bytes())
            workflow = copy / ".github/workflows/windows.yml"
            expected = ".".join(map(str, canonical_version(root)))
            before = workflow.read_text(encoding="utf-8")
            old = f'.version == "{expected}"'
            self.assertIn(old, before)
            workflow.write_text(before.replace(old, '.version == "0.0.0"'), encoding="utf-8")
            with self.assertRaises(VersionPolicyError):
                check_references(copy, canonical_version(copy))

    def test_current_release_assertions_follow_canonical_version(self):
        root = Path(__file__).resolve().parents[1]
        version = ".".join(map(str, canonical_version(root)))
        for filename in (
            "tests/test_customer_release_docs.py",
            "tests/test_windows_customer_hub.py",
            "tests/test_windows_hub.py",
            "tests/test_windows_installer_scripts.py",
        ):
            with self.subTest(filename=filename):
                content = (root / filename).read_text(encoding="utf-8")
                self.assertIn("VERSION = tomllib.loads(", content)
                self.assertNotIn(version, content)


    def test_linux_kde_only_change_keeps_windows_release_without_opening_metadata_gate(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            for filename in ("pyproject.toml", "CHANGELOG.md", *RELEASE_REFERENCES):
                target = copy / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                content = (root / filename).read_text(encoding="utf-8")
                if filename == "pyproject.toml":
                    entry = 'kleinanzeiger-center = "kleinanzeiger.linux_center:main"\n'
                    self.assertEqual(content.count(entry), 1)
                    content = content.replace(entry, "")
                target.write_text(content, encoding="utf-8")

            def git(*args):
                return subprocess.run(
                    ["git", "-c", "user.name=Release Test",
                     "-c", "user.email=test@example.invalid", *args],
                    cwd=copy, check=True, capture_output=True, text=True,
                )

            git("init", "-q")
            git("add", ".")
            git("commit", "-qm", "Windows release baseline")
            version = canonical_version(copy)
            git("tag", "v" + ".".join(map(str, version)))

            linux = copy / "src/kleinanzeiger/linux_center.py"
            linux.parent.mkdir(parents=True)
            linux.write_text("def main():\n    return 0\n", encoding="utf-8")
            pyproject = copy / "pyproject.toml"
            original = pyproject.read_text(encoding="utf-8")
            target = 'kleinanzeiger = "kleinanzeiger.cli:launcher"\n'
            self.assertEqual(original.count(target), 1)
            pyproject.write_text(original.replace(
                target, target + 'kleinanzeiger-center = "kleinanzeiger.linux_center:main"\n'
            ), encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "Linux-only KDE frontend")
            self.assertEqual(check(copy), version)

            # Dependency or Windows changes cannot piggyback on the Linux-only lane.
            pyproject.write_text(pyproject.read_text(encoding="utf-8").replace(
                'requires-python = ">=3.12"', 'requires-python = ">=3.13"',
            ), encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "unexpected package compatibility change")
            with self.assertRaisesRegex(VersionPolicyError, "without a new version"):
                require_bump_for_customer_changes(copy, version, version)

    def test_shipping_change_requires_bump_but_docs_only_do_not(self):
        root = Path(__file__).resolve().parents[1]
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            for filename in ("pyproject.toml", "CHANGELOG.md", *RELEASE_REFERENCES):
                target = copy / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((root / filename).read_bytes())
            source = copy / "src/kleinanzeiger/example.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")

            def git(*args):
                return subprocess.run(
                    ["git", "-c", "user.name=Release Test",
                     "-c", "user.email=test@example.invalid", *args],
                    cwd=copy, check=True, capture_output=True, text=True,
                )

            git("init", "-q")
            git("add", ".")
            git("commit", "-qm", "released customer baseline")
            version = canonical_version(copy)
            git("tag", "v" + ".".join(map(str, version)))
            # README-only maintenance must not trigger an unnecessary ZIP.
            readme = copy / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            self.assertEqual(check(copy), version)

            # A functional change with the same version must not be merged.
            source.write_text("VALUE = 2\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-qm", "change runtime without version")
            with self.assertRaisesRegex(VersionPolicyError, "without a new version"):
                require_bump_for_customer_changes(copy, version, version)
            with self.assertRaisesRegex(VersionPolicyError, "without a new version"):
                check(copy)

            # The official bump command must fix the failure, not deadlock.
            patched = next_version(version, "patch")
            self.assertEqual(bump(copy, "patch"), patched)
            with self.assertRaisesRegex(VersionPolicyError, "Missing release notes"):
                check(copy)
            add_customer_note(copy, patched)
            self.assertEqual(check(copy), patched)


if __name__ == "__main__":
    unittest.main()