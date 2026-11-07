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
            for filename in ("pyproject.toml", *RELEASE_REFERENCES):
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


    def test_shipping_change_requires_bump_but_docs_only_do_not(self):
        root = Path(__file__).resolve().parents[1]
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp)
            for filename in ("pyproject.toml", *RELEASE_REFERENCES):
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
            self.assertEqual(check(copy), patched)


if __name__ == "__main__":
    unittest.main()
