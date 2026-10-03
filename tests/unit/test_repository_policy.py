"""Check repository boundaries without staging or changing the real index."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)


class RepositoryPolicyTests(unittest.TestCase):
    def test_generated_and_private_paths_are_ignored(self) -> None:
        paths = (
            "docs/architecture.md",
            "docs/version.md",
            "docs/exports/nested/report.pdf",
            ".env",
            ".env.production",
            ".secrets/key.pem",
            "data/raw/example.csv",
            "artifacts/example.json",
            ".tools/node/bin/node",
            ".scratch/check/result.json",
            ".venv/bin/python",
            "apps/web/node_modules",
            "apps/example/node_modules/example/index.js",
            "apps/web/.next/server/app.js",
            "apps/web/README.md",
            "reports/result.docx",
            "analysis/notebook.ipynb",
            "dist/fleetiq.whl",
        )
        for path in paths:
            with self.subTest(path=path):
                candidate = Path(path)
                # Git tracks a symlink entry, never files beyond that entry.
                for parent in candidate.parents:
                    if (ROOT / parent).is_symlink():
                        candidate = parent
                        break
                self.assertEqual(
                    git("check-ignore", "--no-index", "-q", candidate.as_posix()).returncode, 0
                )

    def test_source_and_templates_remain_trackable(self) -> None:
        for path in (
            "apps/api/fleetiq_api/main.py",
            "config/features.json",
            "scripts/bootstrap.sh",
            "tests/unit/test_imports.py",
            "pyproject.toml",
            "uv.lock",
            ".env.example",
        ):
            with self.subTest(path=path):
                self.assertEqual(git("check-ignore", "--no-index", "-q", path).returncode, 1)

    def test_no_tracked_or_staged_documents(self) -> None:
        tracked = git("ls-files", "-z")
        staged = git("diff", "--cached", "--name-only", "-z")
        self.assertEqual(tracked.returncode, 0, tracked.stderr)
        self.assertEqual(staged.returncode, 0, staged.stderr)
        forbidden_suffixes = {
            ".md",
            ".pdf",
            ".doc",
            ".docx",
            ".ppt",
            ".pptx",
            ".ipynb",
            ".rtf",
            ".odt",
            ".odp",
        }
        for path in (tracked.stdout + staged.stdout).split("\0"):
            if not path:
                continue
            with self.subTest(path=path):
                self.assertFalse(path.startswith(("docs/", "data/", "artifacts/", ".secrets/")))
                self.assertNotIn(Path(path).suffix.lower(), forbidden_suffixes)
                self.assertFalse(
                    Path(path).name.startswith(".env") and Path(path).name != ".env.example"
                )


if __name__ == "__main__":
    unittest.main()
