#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RD_COCKPIT_ROOT="${RD_COCKPIT_HOME:-$(dirname -- "${SCRIPT_DIR}")}"
RD_PYTHON="${RD_PYTHON:-${RD_COCKPIT_ROOT}/.venv/bin/python}"
if [[ ! -x "${RD_PYTHON}" ]]; then RD_PYTHON="$(command -v python3)"; fi
exec 9>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.lock"
flock -n 9 || exit 0

cd "${RD_COCKPIT_ROOT}"
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,${NO_PROXY}}"
export no_proxy="127.0.0.1,localhost${no_proxy:+,${no_proxy}}"

"${RD_PYTHON}" -m rd_cockpit.historical_reports --workers 1

# Project classification is also cache-backed. A normal nightly scan makes
# no model request; only a new unresolved ASR/其他 record is sent to Codex
# (DeepSeek is the transport/schema fallback).
IFS=':' read -r -a report_dirs <<<"${RD_DAILY_REPORT_LEGACY_DIRS:-${RD_DAILY_REPORT_DIR:-${HOME}/daily-reports}}"
for report_dir in "${report_dirs[@]}"
do
  if [[ -d "${report_dir}" ]]; then
    "${RD_PYTHON}" -m rd_cockpit.project_classifier \
      --report-dir "${report_dir}"
  fi
done

# Recent Agent sessions can reveal a real Git repository that has not yet been
# registered.  Deterministic path evidence creates the candidate; Codex reviews
# only changed candidates.  Registration always requires an explicit CLI
# confirmation, and this hook never edits projects.yaml.
if ! "${RD_COCKPIT_ROOT}/hooks/refresh-project-discovery.sh"; then
  printf '[%s] WARNING: project discovery failed; cached candidates remain available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
fi

# Readable experiment records are derived from the authoritative Daily Report,
# not raw event counts.  The refresh is SHA-bound and normally uses zero model
# calls; a new/changed candidate report is analyzed once.  A fallback sidecar
# remains usable, but is retried by Codex on the next healthy nightly run.
if ! "${RD_COCKPIT_ROOT}/hooks/refresh-experiment-intelligence.sh"; then
  printf '[%s] WARNING: experiment intelligence refresh failed; cached records remain available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
fi

# The architecture refresh has its own lock and source-hash cache.  Opening the
# browser never calls a model; only projects whose source/report evidence
# changed since the last accepted snapshot are analyzed.
if ! "${RD_COCKPIT_ROOT}/hooks/refresh-algorithm-architecture.sh"; then
  printf '[%s] WARNING: algorithm architecture refresh failed; cached snapshots remain available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
fi
