"""Validate tracked/staged code boundaries without exposing file contents."""

import argparse
import re
import subprocess
from pathlib import Path

ROOT_FILES = {
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".python-version",
    ".nvmrc",
    ".env.example",
    "pyproject.toml",
    "uv.lock",
    "compose.yml",
    ".dockerignore",
}
CODE_ROOTS = {
    "apps",
    "services",
    "packages",
    "ml",
    "config",
    "infrastructure",
    "scripts",
    "tests",
    ".github",
}
FORBIDDEN_PARTS = {
    "docs",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
    "test-results",
    "playwright-report",
}
FORBIDDEN_SUFFIXES = {
    ".md",
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".ipynb",
    ".rtf",
    ".odt",
    ".odp",
    ".zip",
    ".parquet",
    ".joblib",
    ".pickle",
    ".pkl",
    ".onnx",
    ".pt",
    ".h5",
    ".db",
    ".sqlite",
    ".tsbuildinfo",
    ".log",
}
SECRET_PATTERNS = [
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\r\n]{20,}"),
]


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE)


def allowed(name: str) -> bool:
    p = Path(name)
    if name in ROOT_FILES:
        return True
    return (
        not p.is_absolute()
        and ".." not in p.parts
        and bool(p.parts)
        and p.parts[0] in CODE_ROOTS
        and not (set(p.parts) & FORBIDDEN_PARTS)
        and not any(part.startswith(".env") for part in p.parts)
        and p.suffix.lower() not in FORBIDDEN_SUFFIXES
        and not any(part in {".secrets", ".venv", ".tools", ".scratch"} for part in p.parts)
    )


def check(root: Path, stage: bool = False) -> None:
    if Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root.resolve():
        raise ValueError("unexpected Git root")
    if "/docs/" not in (root / ".gitignore").read_text().splitlines():
        raise ValueError("/docs/ ignore rule is required")
    tracked = git(root, "ls-files", "-z").decode().split("\0")
    staged = git(root, "diff", "--cached", "--name-only", "-z").decode().split("\0")
    for name in filter(None, tracked + staged):
        if not allowed(name):
            raise ValueError(f"forbidden tracked/staged path: {name}")
    for name in filter(None, staged):
        # Deleted files have no content in the index.
        if subprocess.run(
            ["git", "cat-file", "-e", ":" + name],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            continue
        if any(p.search(git(root, "show", ":" + name)) for p in SECRET_PATTERNS):
            raise ValueError(f"secret-shaped content staged: {name}")
    if stage:
        names = git(
            root, "ls-files", "--modified", "--deleted", "--others", "--exclude-standard", "-z"
        )
        candidates = list(dict.fromkeys(filter(None, names.decode().split("\0"))))
        for name in candidates:
            if not allowed(name):
                raise ValueError(f"unexpected implementation path: {name}")
            p = root / name
            if p.is_symlink():
                raise ValueError(f"implementation symlink requires explicit review: {name}")
            if p.is_file() and any(pattern.search(p.read_bytes()) for pattern in SECRET_PATTERNS):
                raise ValueError(f"secret-shaped content: {name}")
        if candidates:
            subprocess.run(["git", "add", "--", *candidates], cwd=root, check=True)
        check(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", action="store_true")
    options = parser.parse_args()
    try:
        check(Path(__file__).resolve().parents[1], options.stage)
    except (ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Repository policy failed: {exc}\n")
    print("Repository policy passed: implementation only; no staged documents or credentials.")
