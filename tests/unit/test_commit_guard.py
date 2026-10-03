import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Fixture"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.test"], check=True
    )
    (tmp_path / ".gitignore").write_text("/docs/\n.env\n/.secrets\n/data\n/artifacts\n")
    (tmp_path / "scripts").mkdir()
    for name in ("commit-code.sh", "check_repository.py", "env.sh"):
        shutil.copyfile(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    return tmp_path


def guard(repo):
    return subprocess.run(
        ["bash", "scripts/commit-code.sh", "--message", "chore: fixture", "--dry-run"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "name",
    [
        "docs/guide.md",
        ".env",
        "data/raw.csv",
        "apps/web/README.md",
        ".secrets/key.pem",
        "config/model.joblib",
    ],
)
def test_forced_staged_private_paths_fail_before_commit(repo, name):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("touch .executed-env" if name == ".env" else "fixture")
    subprocess.run(["git", "add", "-f", "--", name], cwd=repo, check=True)
    result = guard(repo)
    assert result.returncode != 0
    assert "forbidden" in result.stderr
    assert not (repo / ".executed-env").exists()
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=repo, capture_output=True
        ).returncode
        != 0
    )


def test_staged_secret_content_is_rejected(repo):
    (repo / "config").mkdir()
    (repo / "config/token.json").write_text('"' + "ghp_" + "a" * 36 + '"')
    subprocess.run(["git", "add", "config/token.json"], cwd=repo, check=True)
    assert guard(repo).returncode != 0


def test_dry_run_does_not_stage_or_create_empty_commit(repo):
    before = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo)
    assert guard(repo).returncode == 0
    assert subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=repo) == before
