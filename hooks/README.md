# Agent hook adapter

`rd agent-hook` accepts the official Codex and Claude Code lifecycle payloads.
`python -m rd_cockpit install-hooks` merges the corresponding user-level hook
definitions into `~/.codex/hooks.json` and `~/.claude/settings.json` without
removing existing hooks. The executable adapter is `agent-hook.py`.

The automatic path records Session boundaries and inspects Bash
`PostToolUse`/`PostToolUseFailure` events for tests, benchmarks, training and
evaluation commands. Common metrics are extracted as structured facts. Stop
and PostCompact prose is not collected, and full transcripts are not copied
into the database.

`rd hook` remains available for any source-neutral custom integration:

Start a session:

```bash
export RD_HOOK_KIND=start
printf '%s\n' '{"project_id":"obstacle","goal":"验证 native TensorRT"}' \
  | ./hooks/rd-hook.sh
```

Close only when the whole session really ends:

```bash
export RD_HOOK_KIND=end
printf '%s\n' '{"project_id":"obstacle","session_id":"session_xxx",'\
'"status":"completed","summary":"完成 Jetson 验证并归档结果。",'\
'"results":["Jetson latency 55ms"],"event_id":"session-end-1"}' \
  | ./hooks/rd-hook.sh
```

Supported envelope fields include `project_id`, `session_id`, `goal`,
`summary`, `status`, `results`, `remaining`, `blockers`, `decisions`, `files`,
`evidence_refs`, `repo_path`, `commit_sha`, `dirty`,
`occurred_at`, and a caller-provided `event_id` for idempotency. Hook input is
classified as `reported`; it is never treated as an observed test or Git fact.

The ledger never stores the whole hook envelope. Only the structured fields
above are retained, so prompts and responses do not silently become a second
conversation archive.

The adapter writes only to the local ledger. It does not execute commands,
modify repositories, or control remote machines.

If another local collector temporarily owns the SQLite writer lock, the
interactive hook returns immediately and stores a compact, redacted envelope
under `.rd-cockpit/hook-queue/`. `rd usage-sync --watch` replays that queue at
the end of its next collection cycle. Full prompts and transcripts are not
placed in the queue.
# Project discovery

`refresh-project-discovery.sh` scans the last 30 days of Codex and Claude Code
Session metadata for unregistered Git repositories.  It uses cached Codex
reviews and never edits `config/projects.yaml`; accepting or ignoring a
candidate is always an explicit `rd_cockpit.cli project` command.
