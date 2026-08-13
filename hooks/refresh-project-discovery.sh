#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RD_COCKPIT_ROOT="${RD_COCKPIT_HOME:-$(dirname -- "${SCRIPT_DIR}")}"
RD_PYTHON="${RD_PYTHON:-${RD_COCKPIT_ROOT}/.venv/bin/python}"
if [[ ! -x "${RD_PYTHON}" ]]; then RD_PYTHON="$(command -v python3)"; fi
RD_DISCOVERY_LOG="${RD_PROJECT_DISCOVERY_LOG:-${RD_COCKPIT_ROOT}/.rd-cockpit/project-discovery.log}"

mkdir -p "${RD_COCKPIT_ROOT}/.rd-cockpit"
exec 9>"${RD_COCKPIT_ROOT}/.rd-cockpit/project-discovery.lock"
flock -n 9 || exit 0

cd "${RD_COCKPIT_ROOT}"
{
  printf '[%s] project discovery started\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "${RD_PYTHON}" -m rd_cockpit.cli --home "${RD_COCKPIT_ROOT}" \
    project discover --days 30
  printf '[%s] project discovery completed\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >>"${RD_DISCOVERY_LOG}" 2>&1
