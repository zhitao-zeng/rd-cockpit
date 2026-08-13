# Privacy model

R&D Cockpit is local-first. Research data is not bundled with the source code,
and the application does not upload a ledger, report, transcript, path, Token
counter, GPU sample, or personal calendar setting by itself.

## Data that stays local

The following paths are ignored by Git:

- `.rd-cockpit/` — SQLite ledger, caches, lock files, and hook queues;
- `reports/`, `data/`, `experiments/`, `tmp/`, and generated archives;
- `config/*.local.yaml` and `config/personal.yaml`;
- virtual environments, frontend dependencies, builds, and `.env` files.

`rd init` creates `config/projects.local.yaml` with mode `0600`. Commands that
add a project modify that local file rather than the anonymous tracked
template. Public model evidence and research briefs are empty by default; put
project-specific versions in their matching `*.local.yaml` files.

## Agent and LLM boundaries

- Lifecycle hooks retain structured Session boundaries, commands, test
  outcomes, metrics, and aggregate Token counters. They do not copy full
  prompts or responses into the ledger.
- Deterministic reports and the dashboard work without an LLM.
- Optional semantic commands send a bounded evidence bundle to the model the
  user configures. Review that model's privacy policy before enabling it.
- The bundled `daily-report` Skill locally extracts bounded, redacted Session
  intents, conclusions, tool summaries, and Token counters for the selected
  day. Those ignored sidecars may still contain sensitive research context;
  review the active Agent/provider policy before invoking the Skill.
- The web API binds to `127.0.0.1` by default and has no authentication. Do not
  expose it to a network without an authenticated reverse proxy.

## Before publishing a fork

Run `./scripts/privacy-check.sh --history`. Also inspect ignored files before
changing visibility: ignored means “not committed,” not “safe to upload by
another tool.” If a credential was ever committed, revoke it and rewrite the
history before publication.
