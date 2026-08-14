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

status() {
  "${RD_PYTHON}" -m rd_cockpit.task_status --home "${RD_COCKPIT_ROOT}" \
    --stage "$1" --state "$2" --message "${3:-}"
}

run_stage() {
  local stage="$1"
  shift
  status "${stage}" running
  if "$@"; then
    status "${stage}" ok
    return 0
  fi
  status "${stage}" failed "See the systemd journal or stage log"
  return 1
}

pipeline_failed=0
status pipeline running

run_stage reports "${RD_PYTHON}" -m rd_cockpit.historical_reports --workers 1 || pipeline_failed=1

# Project classification is also cache-backed. A normal nightly scan makes
# no model request; only a new unresolved ASR/其他 record is sent to Codex
# (DeepSeek is the transport/schema fallback).
IFS=':' read -r -a report_dirs <<<"${RD_DAILY_REPORT_LEGACY_DIRS:-${RD_DAILY_REPORT_DIR:-${HOME}/daily-reports}}"
status classification running
classification_failed=0
for report_dir in "${report_dirs[@]}"
do
  if [[ -d "${report_dir}" ]]; then
    "${RD_PYTHON}" -m rd_cockpit.project_classifier \
      --report-dir "${report_dir}" || classification_failed=1
  fi
done
if [[ "${classification_failed}" -eq 0 ]]; then
  status classification ok
else
  status classification failed "One or more report directories failed"
  pipeline_failed=1
fi

# Project Pulse, unknowns, breakthroughs and storyline use a separate,
# evidence-bound audit.  Cache hits are local; only new/changed reports or
# user-rated items invoke Codex, with DeepSeek as a schema/availability fallback.
if ! run_stage intelligence "${RD_PYTHON}" -m rd_cockpit.cli \
  --home "${RD_COCKPIT_ROOT}" intelligence-backfill --days 90; then
  printf '[%s] WARNING: project intelligence refresh failed; last-good snapshots remain available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
  pipeline_failed=1
fi

# Recent Agent sessions can reveal a real Git repository that has not yet been
# registered.  Deterministic path evidence creates the candidate; Codex reviews
# only changed candidates.  Registration always requires an explicit CLI
# confirmation, and this hook never edits projects.yaml.
if ! run_stage discovery "${RD_COCKPIT_ROOT}/hooks/refresh-project-discovery.sh"; then
  printf '[%s] WARNING: project discovery failed; cached candidates remain available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
  pipeline_failed=1
fi

# Readable experiment records are derived from the authoritative Daily Report,
# not raw event counts.  The refresh is SHA-bound and normally uses zero model
# calls; a new/changed candidate report is analyzed once.  A fallback sidecar
# remains usable, but is retried by Codex on the next healthy nightly run.
if ! run_stage experiments "${RD_COCKPIT_ROOT}/hooks/refresh-experiment-intelligence.sh"; then
  printf '[%s] WARNING: experiment intelligence refresh failed; cached records remain available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
  pipeline_failed=1
fi

# The architecture refresh has its own lock and source-hash cache.  Opening the
# browser never calls a model; only projects whose source/report evidence
# changed since the last accepted snapshot are analyzed.
if ! run_stage architecture "${RD_COCKPIT_ROOT}/hooks/refresh-algorithm-architecture.sh"; then
  printf '[%s] WARNING: algorithm architecture refresh failed; cached snapshots remain available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
  pipeline_failed=1
fi

# Paper search and Chinese summaries are strictly background work.  The web
# endpoint only reads the last atomic snapshot and can therefore never spend
# tokens or wait on OpenAlex while a page is loading.
if ! run_stage radar "${RD_PYTHON}" -m rd_cockpit.cli --home "${RD_COCKPIT_ROOT}" radar-refresh; then
  printf '[%s] WARNING: research radar refresh failed; cached papers remain available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
  pipeline_failed=1
fi

# Expensive multi-day projections are materialized once after their semantic
# inputs settle. Browser requests then read an atomic JSON snapshot and never
# spend several seconds reparsing the report history.
if ! run_stage views "${RD_PYTHON}" -m rd_cockpit.cli --home "${RD_COCKPIT_ROOT}" materialize-views; then
  printf '[%s] WARNING: dashboard view materialization failed; on-demand fallback remains available\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >>"${RD_COCKPIT_ROOT}/.rd-cockpit/report-normalize.log"
  pipeline_failed=1
fi

if [[ "${pipeline_failed}" -eq 0 ]]; then
  status pipeline ok
else
  status pipeline failed "At least one refresh stage failed"
  exit 1
fi
