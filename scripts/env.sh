# Source this trusted local development environment from WSL Ubuntu.
task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$task_project_root/.tools/node/bin:$HOME/.local/bin:$PATH"
export UV_LINK_MODE=copy
# Keep Linux package installs off the OneDrive/DrvFS tree. Each checkout gets its own
# environment; the previous project .venv remains intact for recovery.
task_python_environment_key="$(printf '%s\n' "$task_project_root" | sha256sum | cut -c1-12)"
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.local/share/fleetiq/python312-$task_python_environment_key}"
unset task_python_environment_key
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
