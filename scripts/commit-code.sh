#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
message=''
dry_run=0
while (($#)); do
  case "$1" in
    --message) test "$#" -ge 2; message="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) printf 'Unknown argument\n' >&2; exit 2 ;;
  esac
done
if [[ ! "$message" =~ ^(feat|fix|refactor|test|chore|perf)(\([a-zA-Z0-9_-]+\))?:\ .+ ]] || [[ "$message" == *$'\n'* ]]; then
  printf 'A single-line Conventional Commit message is required.\n' >&2
  exit 2
fi
python3 scripts/check_repository.py
git var GIT_AUTHOR_IDENT >/dev/null
if ((dry_run)); then
  printf 'Policy review complete; no staging or commit performed.\n'
  exit 0
fi
source scripts/env.sh
uv run ruff check .
uv run ruff format --check .
uv run pytest -m 'not integration and not e2e'
if git ls-files --modified --others --exclude-standard | grep -q '^apps/web/'; then
  npm --prefix apps/web run lint
  npm --prefix apps/web run typecheck
  npm --prefix apps/web run format:check
fi
python3 scripts/check_repository.py --stage
git diff --cached --check
git diff --cached --stat
if git diff --cached --quiet; then
  printf 'No eligible changes; no empty commit created.\n'
  exit 0
fi
git commit -m "$message"
