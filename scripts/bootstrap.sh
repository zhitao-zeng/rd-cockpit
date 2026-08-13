#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname -- "${SCRIPT_DIR}")"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "${ROOT}"
if command -v uv >/dev/null 2>&1; then
  uv venv --python "${PYTHON_BIN}" .venv
  uv pip install --python .venv/bin/python -e '.[server,dev]'
else
  "${PYTHON_BIN}" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -e '.[server,dev]'
fi

if ! command -v npm >/dev/null 2>&1; then
  printf 'npm is required for the web dashboard. Install Node.js 20+ and rerun.\n' >&2
  exit 1
fi
NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])')"
if [[ "${NODE_MAJOR}" -lt 20 ]]; then
  printf 'Node.js 20+ is required; found %s. Activate a newer Node.js runtime and rerun.\n' "$(node --version)" >&2
  exit 1
fi
npm --prefix frontend ci
.venv/bin/python -m rd_cockpit init

printf '\nSetup complete.\n'
printf '1. Register a repository with: .venv/bin/rd project add PROJECT_ID --name NAME --repo PATH\n'
printf '2. Install the Daily Report Skill: ./scripts/install-skill.sh --all\n'
printf '3. Start the local UI with:         ./scripts/start.sh\n'
