#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname -- "${SCRIPT_DIR}")"
PYTHON="${RD_PYTHON:-${ROOT}/.venv/bin/python}"
NODE="${RD_NODE_BIN:-$(command -v node || true)}"
if [[ -z "${NODE}" || "$("${NODE}" -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || printf 0)" -lt 20 ]]; then
  NODE="$(find /usr/local -maxdepth 3 -path '*/bin/node' -type f -executable 2>/dev/null | sort -V | tail -1)"
fi
NODE_DIR="$(dirname "${NODE:-/missing}")"
NPM="${RD_NPM_BIN:-${NODE_DIR}/npm}"
CODEX="$(command -v codex || true)"
WEB_HOST="${RD_WEB_HOST:-127.0.0.1}"
UNIT_SOURCE="${ROOT}/deploy/systemd"
UNIT_TARGET="${HOME}/.config/systemd/user"
ENV_DIR="${HOME}/.config/rd-cockpit"
ENV_FILE="${ENV_DIR}/env"

if [[ ! -x "${PYTHON}" ]]; then
  printf 'Python environment is missing: %s\n' "${PYTHON}" >&2
  exit 1
fi
if [[ -z "${NODE}" || ! -x "${NODE}" || "$("${NODE}" -p 'Number(process.versions.node.split(".")[0])')" -lt 20 ]]; then
  printf 'Node.js 20+ is required for the web service.\n' >&2
  exit 1
fi
if [[ ! -e "${NPM}" ]]; then
  printf 'npm from the selected Node.js installation is required: %s\n' "${NPM}" >&2
  exit 1
fi

# Build once during installation. The long-running web service serves these
# immutable assets and no longer keeps Vite/esbuild file watchers alive.
PATH="${NODE_DIR}:/usr/local/bin:/usr/bin:/bin" VITE_API_BASE_URL= \
  "${NPM}" --prefix "${ROOT}/frontend" run build

mkdir -p "${UNIT_TARGET}" "${ENV_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
  umask 077
  {
    printf 'RD_COCKPIT_HOME=%s\n' "${ROOT}"
    if [[ -n "${CODEX}" ]]; then printf 'RD_CODEX_BIN=%s\n' "${CODEX}"; fi
  } >"${ENV_FILE}"
fi

# A LAN-bound dashboard needs an application-level boundary because the API
# intentionally contains personal research records. Keep localhost zero-setup;
# generate a private browser token for every non-loopback installation.
if [[ "${WEB_HOST}" != "127.0.0.1" && "${WEB_HOST}" != "localhost" && "${WEB_HOST}" != "::1" ]]; then
  EXISTING_API_TOKEN="$(awk -F= '$1 == "RD_API_TOKEN" {value=substr($0,index($0,"=")+1)} END {print value}' "${ENV_FILE}")"
  API_TOKEN="${RD_API_TOKEN:-${EXISTING_API_TOKEN}}"
  if [[ -z "${API_TOKEN}" ]]; then
    API_TOKEN="$("${PYTHON}" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  fi
  if [[ ! "${API_TOKEN}" =~ ^[A-Za-z0-9._~-]+$ ]]; then
    printf 'RD_API_TOKEN may contain only URL-safe token characters.\n' >&2
    exit 1
  fi
  if [[ "${API_TOKEN}" != "${EXISTING_API_TOKEN}" ]]; then
    TOKEN_ENV_TEMP="$(mktemp "${ENV_DIR}/.env.XXXXXX")"
    awk '$0 !~ /^RD_API_TOKEN=/' "${ENV_FILE}" >"${TOKEN_ENV_TEMP}"
    printf 'RD_API_TOKEN=%s\n' "${API_TOKEN}" >>"${TOKEN_ENV_TEMP}"
    chmod 600 "${TOKEN_ENV_TEMP}"
    mv "${TOKEN_ENV_TEMP}" "${ENV_FILE}"
  fi
fi
chmod 600 "${ENV_FILE}"

for template in "${UNIT_SOURCE}"/*.service.in
do
  target="${UNIT_TARGET}/$(basename "${template}" .in)"
  sed -e "s|@ROOT@|${ROOT}|g" \
      -e "s|@PYTHON@|${PYTHON}|g" \
      -e "s|@NPM@|${NPM}|g" \
      -e "s|@NODE_DIR@|${NODE_DIR}|g" \
      -e "s|@WEB_HOST@|${WEB_HOST}|g" \
      "${template}" >"${target}"
done
cp "${UNIT_SOURCE}/rd-cockpit-refresh.timer" "${UNIT_TARGET}/rd-cockpit-refresh.timer"
cp "${UNIT_SOURCE}/rd-cockpit-maintenance.timer" "${UNIT_TARGET}/rd-cockpit-maintenance.timer"

# A previous development install may have created usage-sync as a transient
# unit.  Stop it before reloading so systemd adopts the versioned unit file.
systemctl --user stop rd-cockpit-usage-sync.service 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now \
  rd-cockpit-resources.service rd-cockpit-usage-sync.service rd-cockpit-web.service \
  rd-cockpit-refresh.timer rd-cockpit-maintenance.timer
systemctl --user restart \
  rd-cockpit-resources.service rd-cockpit-usage-sync.service rd-cockpit-web.service

if [[ "${RD_ENABLE_STANDALONE_API:-0}" == "1" ]]; then
  systemctl --user enable --now rd-cockpit-api.service
  systemctl --user restart rd-cockpit-api.service
else
  # The production web process already serves the same API under /api. Keep
  # port 8787 as an explicit development/compatibility option, not a duplicate
  # always-on process.
  systemctl --user disable --now rd-cockpit-api.service 2>/dev/null || true
fi

printf 'Installed persistent services, nightly refresh and maintenance timers.\n'
