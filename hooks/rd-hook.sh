#!/usr/bin/env bash
set -euo pipefail

# Source-neutral adapter for Claude Code, Codex wrappers, or shell hooks.
# The caller supplies JSON on stdin and selects start/end through
# RD_HOOK_KIND. Keep the home explicit so hooks can run from any repository.
COCKPIT_HOME="${RD_COCKPIT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
KIND="${RD_HOOK_KIND:?set RD_HOOK_KIND=start|end}"
exec "${RD_PYTHON:-python3}" -m rd_cockpit --home "$COCKPIT_HOME" hook --kind "$KIND" "$@"
