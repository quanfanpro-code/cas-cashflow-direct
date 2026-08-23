from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_release import build_release
from cashflow_direct.versions import current_versions


ROOT = Path(__file__).resolve().parents[1]


class ReleaseBundleTests(unittest.TestCase):
    def test_version_bundle_covers_accumulation_and_forced_checks(self) -> None:
        versions = current_versions(ROOT)

        self.assertTrue(versions["materiality_and_accumulation"])
        self.assertTrue(versions["forced_checks"])

    def test_release_contains_only_runtime_whitelist_and_runs_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release = build_release(ROOT, Path(tmp) / "cas-cashflow-direct")
            names = {path.name for path in release.root.iterdir()}
            self.assertEqual(
                {"SKILL.md", "README.md", "LICENSE", "release_manifest.json", "references", "scripts", "src"},
                names,
            )
            self.assertFalse((release.root / "docs").exists())
            self.assertFalse((release.root / "tests").exists())
            self.assertFalse((release.root / ".git").exists())
            self.assertFalse((release.root / "CONTEXT.md").exists())
            manifest = json.loads(
                (release.root / "release_manifest.json").read_text(encoding="utf-8-sig")
            )
            self.assertEqual(sorted(manifest["files"]), manifest["files"])
            completed = subprocess.run(
                [sys.executable, str(release.root / "scripts" / "run_cashflow_direct.py"), "--help"],
                cwd=release.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "UTF-8", "PYTHONUTF8": "1"},
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue(release.zip_path.is_file())

    def test_existing_release_target_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "existing"
            target.mkdir()
            with self.assertRaisesRegex(FileExistsError, "不会覆盖"):
                build_release(ROOT, target)


if __name__ == "__main__":
    unittest.main()
