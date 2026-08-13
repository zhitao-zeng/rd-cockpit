# R&D Cockpit

A local-first research record and project-intelligence dashboard for people
working across multiple repositories and Agent Sessions.

R&D Cockpit starts from readable Daily Reports. Git, tests, experiments,
Codex/Claude Code lifecycle events, Token counters, and optional GPU samples
act as supporting evidence instead of becoming an unreadable event feed.

## What it provides

- daily and weekly research records with project attribution;
- project pulse, storyline, breakthroughs, open unknowns, and change summaries;
- experiment narratives, metric trends, conclusions, and knowledge summaries;
- algorithm architecture views backed by repository/report evidence;
- aggregate Codex and Claude Code Session and Token usage;
- project discovery from Agent Sessions, with explicit confirmation;
- read-only resource, verification, anomaly, and reproducibility views;
- a React/ECharts dashboard plus CLI and read-only MCP tools.

All core views work without an LLM. Optional semantic analysis is cached and
must cite the evidence bundle supplied to it.

## Quick start

Requirements: Python 3.10+, Node.js 20+, npm, and Git. `uv` is recommended but
not required.

```bash
git clone https://github.com/zhitao-zeng/rd-cockpit.git
cd rd-cockpit
./scripts/bootstrap.sh
./scripts/install-skill.sh --all

.venv/bin/rd project add speech_research \
  --name "Speech Research" \
  --repo "$HOME/code/speech-research" \
  --keyword ASR \
  --stage implementation \
  --stage local_eval \
  --stage delivery

./scripts/start.sh
```

Open <http://127.0.0.1:4016>. The API listens only on
<http://127.0.0.1:8787>.

The optional installer links the bundled, privacy-safe `daily-report` Skill
into Codex and Claude Code. Invoke it as `$daily-report` or ask for “生成今天的
日报”. It reads only bounded local Session excerpts, registered repositories,
and the previous plan; the validator rejects unsupported evidence and numbers.
Installing the Skill does not create a cron job or send data anywhere.

The first initialization creates `config/projects.local.yaml`; it is private,
mode `0600`, and ignored by Git. The tracked `config/projects.yaml` remains an
anonymous empty template.

## Bring your Daily Reports

By default reports are read from `~/daily-reports`. Each file is named
`YYYY-MM-DD.md` and can contain sections such as:

```markdown
# 日报 2026-01-15

## Speech Research

### Streaming decoder evaluation
- **做了什么**：Compared two decoding strategies on the held-out set.
- **为什么**：Reduce latency without changing the acoustic model.
- **结果**：Strategy B reduced median latency; remote validation is pending.
- **关键文件**：`results/streaming-eval.json`
```

Use another directory without changing source files:

```bash
export RD_DAILY_REPORT_DIR="$HOME/research-reports"
```

Historical normalization and higher-level analysis write only ignored sidecar
files beside the reports. Original Markdown is never rewritten.

The bundled Skill generates this same structure. Its implementation lives in
[`skills/daily-report`](skills/daily-report), so forks can inspect and customize
the collection and audit rules before installation.

## Agent lifecycle integration

After reviewing the generated commands, install local lifecycle hooks:

```bash
.venv/bin/rd install-hooks
```

The hooks record Session boundaries and structured Bash outcomes. They do not
copy full prompts or model responses. See [hooks/README.md](hooks/README.md).

Useful commands:

```bash
.venv/bin/rd status
.venv/bin/rd resume speech_research
.venv/bin/rd run --project speech_research --type test -- pytest -q
.venv/bin/rd daily
.venv/bin/rd weekly
.venv/bin/rd since "yesterday"
.venv/bin/rd project discover --days 30
.venv/bin/rd algorithm-analyze speech_research
.venv/bin/rd experiment-backfill --days 90 --project speech_research
```

## Configuration

Private local files take precedence over their anonymous tracked counterpart:

| Local file | Purpose |
| --- | --- |
| `config/projects.local.yaml` | repositories, matching rules, stages, research topics |
| `config/personal.yaml` | optional life-bar dates and leave balance |
| `config/model-evidence.local.yaml` | reviewed public model-family references |
| `config/project-research-briefs.local.yaml` | curated project research reviews |

Examples are available in `config/*.example.yaml`. Environment variables are
documented in [.env.example](.env.example).

## Privacy and security

Generated databases, reports, Session caches, archives, local configuration,
and credentials are excluded from Git. Run this before publishing a fork:

```bash
./scripts/privacy-check.sh --history
```

Read [PRIVACY.md](PRIVACY.md) before enabling an external model or exposing the
API. The API has no authentication and must remain localhost-only unless an
authenticated reverse proxy is added.

## Development

```bash
.venv/bin/pytest
npm --prefix frontend test
npm --prefix frontend run build
./scripts/privacy-check.sh
```

## License

[MIT](LICENSE)
