"""Real synthetic negative tests for the immutable offline wheel builder."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "docs" / "mark-api-container-build.sh"


class OfflineBuildDependencyTests(unittest.TestCase):
    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temp = tempfile.TemporaryDirectory(prefix="mark-build-dependency-")
        root = Path(temp.name) / "checkout"
        (root / "docs").mkdir(parents=True)
        (root / "src" / "mark_api").mkdir(parents=True)
        (root / "docs" / BUILDER.name).write_bytes(BUILDER.read_bytes())
        (root / "README.md").write_text("synthetic release fixture\n")
        (root / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
        (root / "src" / "mark_api" / "__init__.py").write_text("")
        subprocess.run(["/usr/bin/git", "init", "-q", str(root)], check=True)
        subprocess.run(["/usr/bin/git", "-C", str(root), "add", "."], check=True)
        subprocess.run([
            "/usr/bin/git", "-C", str(root),
            "-c", "user.name=Release fixture",
            "-c", "user.email=release-fixture@example.invalid",
            "commit", "-qm", "fixture",
        ], check=True)
        sha = subprocess.check_output(
            ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        return temp, root, sha

    def _run_builder(self, root: Path, sha: str, wheel: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("DOCKER_HOST", None)
        env.pop("DOCKER_CONTEXT", None)
        env["MARK_SETUPTOOLS_WHEEL"] = wheel
        return subprocess.run(
            ["/bin/sh", str(root / "docs" / BUILDER.name), sha],
            cwd=root, env=env, text=True, capture_output=True, timeout=45,
            check=False,
        )

    def test_backend_is_exactly_pinned(self) -> None:
        import tomllib
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(project["build-system"]["requires"],
                         ["setuptools==80.9.0"])

    def test_missing_dependency_is_rejected_before_builder_runs(self) -> None:
        temp, root, sha = self._fixture()
        with temp:
            result = self._run_builder(root, sha, "")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pinned setuptools wheel is required", result.stderr)

    def test_wrong_bytes_are_rejected_before_builder_runs(self) -> None:
        temp, root, sha = self._fixture()
        with temp:
            wheel = Path(temp.name) / "setuptools-80.9.0-py3-none-any.whl"
            wheel.write_bytes(b"not the reviewed setuptools artifact")
            result = self._run_builder(root, sha, str(wheel))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pinned setuptools wheel checksum mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
