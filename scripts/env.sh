# Source this trusted local development environment from WSL Ubuntu.
task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$task_project_root/.tools/node/bin:$HOME/.local/bin:$PATH"
export UV_LINK_MODE=copy
export NEXT_TELEMETRY_DISABLED=1
if test -f "$task_project_root/.env"; then
  set -a
  source "$task_project_root/.env"
  set +a
fi
if test -f "$task_project_root/config/images.env"; then
  set -a
  source "$task_project_root/config/images.env"
  set +a
fi
unset task_project_root
