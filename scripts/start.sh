#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname -- "${SCRIPT_DIR}")"
API_PORT="${RD_API_PORT:-8787}"
WEB_PORT="${RD_WEB_PORT:-4016}"

if [[ ! -x "${ROOT}/.venv/bin/python" ]]; then
  printf 'Run ./scripts/bootstrap.sh first.\n' >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1 || [[ "$(node -p 'Number(process.versions.node.split(".")[0])')" -lt 20 ]]; then
  printf 'Node.js 20+ is required.\n' >&2
  exit 1
fi

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then kill "${API_PID}" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

cd "${ROOT}"
"${ROOT}/.venv/bin/python" -m rd_cockpit serve --host 127.0.0.1 --port "${API_PORT}" &
API_PID=$!

printf 'R&D Cockpit: http://127.0.0.1:%s\n' "${WEB_PORT}"
VITE_API_PROXY_TARGET="http://127.0.0.1:${API_PORT}" \
  VITE_API_BASE_URL="" \
  npm --prefix frontend run dev -- --host 127.0.0.1 --port "${WEB_PORT}"
