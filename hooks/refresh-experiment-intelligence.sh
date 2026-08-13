#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RD_COCKPIT_ROOT="${RD_COCKPIT_HOME:-$(dirname -- "${SCRIPT_DIR}")}"
RD_PYTHON="${RD_PYTHON:-${RD_COCKPIT_ROOT}/.venv/bin/python}"
if [[ ! -x "${RD_PYTHON}" ]]; then RD_PYTHON="$(command -v python3)"; fi
RD_EXPERIMENT_LOG="${RD_EXPERIMENT_REFRESH_LOG:-${RD_COCKPIT_ROOT}/.rd-cockpit/experiment-intelligence.log}"

mkdir -p "${RD_COCKPIT_ROOT}/.rd-cockpit"
exec 9>"${RD_COCKPIT_ROOT}/.rd-cockpit/experiment-intelligence.lock"
flock -n 9 || exit 0

cd "${RD_COCKPIT_ROOT}"
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,${NO_PROXY}}"
export no_proxy="127.0.0.1,localhost${no_proxy:+,${no_proxy}}"

{
  printf '[%s] experiment intelligence refresh started\n' "$(date '+%Y-%m-%d %H:%M:%S')"
  "${RD_PYTHON}" -m rd_cockpit.cli --home "${RD_COCKPIT_ROOT}" \
    experiment-backfill --days 90 --batch-days 7
  printf '[%s] experiment intelligence refresh completed\n' "$(date '+%Y-%m-%d %H:%M:%S')"
} >>"${RD_EXPERIMENT_LOG}" 2>&1
