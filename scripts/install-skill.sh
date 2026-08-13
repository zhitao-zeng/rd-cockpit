#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname -- "${SCRIPT_DIR}")"
SOURCE="${ROOT}/skills/daily-report"

install_link() {
  local parent="$1"
  local target="${parent}/daily-report"
  mkdir -p "${parent}"
  if [[ -L "${target}" ]] && [[ "$(readlink -f "${target}")" == "$(readlink -f "${SOURCE}")" ]]; then
    printf 'Already installed: %s\n' "${target}"
    return
  fi
  if [[ -e "${target}" || -L "${target}" ]]; then
    printf 'Refusing to replace existing skill: %s\n' "${target}" >&2
    printf 'Move or remove it explicitly, then rerun this installer.\n' >&2
    return 1
  fi
  ln -s "${SOURCE}" "${target}"
  printf 'Installed: %s -> %s\n' "${target}" "${SOURCE}"
}

case "${1:---all}" in
  --codex) install_link "${CODEX_HOME:-${HOME}/.codex}/skills" ;;
  --claude) install_link "${HOME}/.claude/skills" ;;
  --all)
    install_link "${CODEX_HOME:-${HOME}/.codex}/skills"
    install_link "${HOME}/.claude/skills"
    ;;
  *)
    printf 'Usage: %s [--all|--codex|--claude]\n' "$0" >&2
    exit 2
    ;;
esac
