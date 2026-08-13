#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

failed=0
for forbidden in '.rd-cockpit/' 'reports/' 'data/' 'tmp/' 'config/personal.yaml'; do
  if git ls-files | grep -q "^${forbidden}"; then
    printf 'ERROR: generated/private path is tracked: %s\n' "${forbidden}" >&2
    failed=1
  fi
done
if git ls-files | grep -Eq '^config/.*\.local\.yaml$'; then
  printf 'ERROR: a private local config is tracked.\n' >&2
  failed=1
fi

privacy_pattern='(/home/[^$<{[:space:]]+|/mnt/disk[0-9]+/|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3})'
secret_pattern='(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY|Bearer [A-Za-z0-9._-]{20,})'

if matches="$(git grep -IlE "${privacy_pattern}" -- ':!scripts/privacy-check.sh' 2>/dev/null)" && [[ -n "${matches}" ]]; then
  printf 'ERROR: machine-specific path or private address found in:\n%s\n' "${matches}" >&2
  failed=1
fi
if matches="$(git grep -IlE "${secret_pattern}" -- ':!scripts/privacy-check.sh' 2>/dev/null)" && [[ -n "${matches}" ]]; then
  printf 'ERROR: secret-like value found in:\n%s\n' "${matches}" >&2
  failed=1
fi

if [[ "${1:-}" == "--history" ]]; then
  history_matches="$(for commit in $(git rev-list --all); do
    git grep -IlE "${privacy_pattern}|${secret_pattern}" "${commit}" -- ':!scripts/privacy-check.sh' 2>/dev/null || true
  done | sort -u)"
  if [[ -n "${history_matches}" ]]; then
    printf 'ERROR: private material remains in Git history:\n%s\n' "${history_matches}" >&2
    failed=1
  fi
fi

if [[ "${failed}" -ne 0 ]]; then exit 1; fi
printf 'Privacy check passed.\n'
