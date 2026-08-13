---
name: daily-report
description: Generate or rebuild an evidence-audited research Daily Report from local Codex/Claude Code Session excerpts, registered Git repositories, changed files, Token counters, and the previous day's plan. Use when the user asks for a 日报, daily report, a summary of today's work, or a report for a specified YYYY-MM-DD date.
---

# Daily Report

Generate one readable research report without turning raw Git or Token events
into accomplishments. Treat the report as the primary human record consumed by
R&D Cockpit.

## Workflow

1. Resolve the date from `--date YYYY-MM-DD` or the user's message. Use the
   local calendar date when none is supplied.
2. From the R&D Cockpit repository, run:

   ```bash
   .venv/bin/python skills/daily-report/scripts/collect_daily.py --date YYYY-MM-DD
   ```

   The command prints the private paths of an audit bundle, candidate file,
   validated file, report file, and previous report. Never paste those paths or
   their raw contents into the final chat response.
3. Read the generated `audit_bundle`. Read
   [references/audit-schema.md](references/audit-schema.md) completely, then
   create the JSON object described there at `candidate_file`.
4. Validate the candidate before writing prose:

   ```bash
   .venv/bin/python -m rd_cockpit.daily_audit validate \
     --bundle AUDIT_BUNDLE \
     --model-output CANDIDATE_FILE \
     --requested-model current-agent \
     --repair-unsafe-claims \
     --output VALIDATED_FILE
   ```

   If validation fails, correct the JSON once using the error and source
   evidence. Do not weaken the validator or invent replacement evidence.
5. Render deterministically:

   ```bash
   .venv/bin/python -m rd_cockpit.daily_audit render \
     --audit VALIDATED_FILE \
     --output REPORT_FILE
   ```

6. Confirm the report contains the required sections and briefly tell the user
   which date and projects were covered. Do not run another summarization pass:
   the validated JSON already contains the readable analysis.

## Reporting rules

- Start from Session meaning: what was attempted, why, what result was reached,
  and what remains. Use Git, files, tests, metrics, and Token data only as
  evidence or objective counts.
- Merge duplicate descriptions of the same work across Agents. Keep distinct
  experiments, decisions, and deliverables separate even when they share a
  project.
- Record a result only when the source contains a test, metric, artifact, or
  explicit reusable conclusion. A user request, plan, commit count, or tool
  count is not a result.
- Copy evidence references exactly from `allowed_evidence_refs`. Never invent a
  Session ID, commit, file, metric, project, or completion state.
- Preserve `observed`, `reported`, `inferred`, and `confirmed`. Agent prose is
  normally `reported`; Git and parsed test facts may be `observed`.
- Use only IDs from `project_catalog`. Leave ambiguous work `unassigned` and
  explain the gap instead of guessing.
- Mark yesterday's plan `completed` or `partially_completed` only when evidence
  proves the acceptance object itself. Otherwise use `blocked`, `deferred`,
  `no_evidence`, or `cancelled`.
- Keep numbers only when they occur in cited evidence. Do not estimate cost,
  time, or model performance.
- Keep conclusions scoped. Separate current facts from hypotheses and next
  actions.
- Never include credentials, full prompts/responses, internal network
  addresses, or unrelated private files in the report.

## Existing reports

If `REPORT_FILE` already exists and the user did not explicitly ask to replace
or rebuild it, render to `REPORT_FILE.review.md` and report the difference. An
explicit request to generate, rebuild, recover, or update that date authorizes
replacing its generated report after validation succeeds.
