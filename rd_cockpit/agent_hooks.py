"""Ingest real Codex and Claude Code lifecycle hooks.

The collector keeps only Session boundaries and structured facts extracted
from actual Bash tool results. The formal Daily Report remains the readable
source of truth.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .agent_usage import _project_for_cwd, _workspace_paths
from .config import load_config, machine_name, project_config
from .git_collect import snapshot
from .ledger import Ledger, utc_now
from .security import redact_text, redact_value


_BENCHMARK_RE = re.compile(r"(?:benchmark|bench(?:mark)?[_.-]|trtexec|nsys\s+profile|latency[_-]?test)", re.I)
_EXPERIMENT_RE = re.compile(
    r"(?:train(?:ing)?[_.-]|finetune|fine[-_ ]?tune|evaluate|evaluation|eval[_.-]|"
    r"ablation|sweep|inference|predict|score[_-]|wer[_-]|cer[_-])", re.I,
)
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.S)
_METRIC_RE = re.compile(
    r"(?P<name>1[-_ ]?cer|wer|cer|accuracy|acc|precision|recall|f1(?:[-_ ]?score)?|"
    r"map|bleu|rouge|loss|latency(?:_ms)?|rtf|throughput|fps)"
    r"\s*(?:=|:|is\s+)\s*(?P<value>-?\d+(?:\.\d+)?(?:e[-+]?\d+)?)\s*(?P<unit>%|ms|s|fps)?",
    re.I,
)
_TEST_COUNT_RE = re.compile(r"(?P<count>\d+)\s+(?P<status>passed|failed|skipped|xfailed|error(?:s)?)\b", re.I)


def _payload(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(row["payload_json"])
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _event_time(incoming: dict[str, Any]) -> str:
    # Official hook payloads do not promise a timestamp.  Accept one from
    # wrappers/tests when present and otherwise record ingestion time.
    return str(incoming.get("occurred_at") or incoming.get("timestamp") or utc_now())


def _evidence(incoming: dict[str, Any]) -> list[dict[str, Any]]:
    path = incoming.get("transcript_path")
    return ([{"type": "agent_transcript", "path": str(path),
              "metadata": {"note": "format is host-owned and may change"}}]
            if path else [])


def _command(incoming: dict[str, Any]) -> str:
    tool_input = incoming.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    value = tool_input.get("command", tool_input.get("cmd", ""))
    if isinstance(value, list):
        try:
            return shlex.join(str(item) for item in value)
        except (TypeError, ValueError):
            return " ".join(str(item) for item in value)
    return str(value or "")


def _response_text(incoming: dict[str, Any]) -> str:
    response = incoming.get("tool_response", incoming.get("tool_result", incoming.get("error", "")))
    if isinstance(response, str):
        return redact_text(response)
    if isinstance(response, dict):
        preferred = [response.get(key) for key in ("stdout", "stderr", "output", "content", "error")]
        values = [value for value in preferred if isinstance(value, str) and value]
        if values:
            return redact_text("\n".join(values))
    try:
        return redact_text(json.dumps(response, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        return redact_text(str(response))


def _failed(incoming: dict[str, Any], response: str) -> bool:
    if incoming.get("hook_event_name") == "PostToolUseFailure":
        return True
    raw = incoming.get("tool_response")
    if isinstance(raw, dict):
        if raw.get("is_error") is True or raw.get("success") is False:
            return True
        code = raw.get("exit_code", raw.get("exitCode"))
        if code is not None:
            try:
                return int(code) != 0
            except (TypeError, ValueError):
                pass
    match = re.search(r"(?:exit(?:ed)?(?:\s+with)?(?:\s+code)?|exit_code)\D{0,8}(-?\d+)", response, re.I)
    return bool(match and int(match.group(1)) != 0)


def _shell_invocations(command: str) -> list[tuple[str, list[str]]]:
    """Return actual executable/module targets, not incidental argument text."""
    invocations: list[tuple[str, list[str]]] = []
    for segment in re.split(r"(?:&&|\|\||[;|\n])", command):
        try:
            tokens = shlex.split(segment.strip())
        except ValueError:
            continue
        while tokens and _ENV_ASSIGNMENT_RE.match(tokens[0]):
            tokens.pop(0)
        if not tokens or tokens[0] in {"cd", "export", "set", "source", ".", "echo", "printf"}:
            continue
        while tokens and Path(tokens[0]).name in {"env", "nohup", "command"}:
            tokens.pop(0)
            while tokens and (_ENV_ASSIGNMENT_RE.match(tokens[0]) or tokens[0].startswith("-")):
                tokens.pop(0)
        if not tokens:
            continue
        if Path(tokens[0]).name in {"uv", "poetry"} and len(tokens) > 1 and tokens[1] == "run":
            tokens = tokens[2:]
        if not tokens:
            continue
        executable = Path(tokens[0]).name.casefold()
        args = tokens[1:]
        if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", executable):
            if "-m" in args:
                index = args.index("-m")
                if index + 1 < len(args):
                    invocations.append((args[index + 1].casefold(), args[index + 2:]))
                continue
            target = next((value for value in args if not value.startswith("-")), "")
            if not target or target in {"-", "<<PY", "<<'PY'", '<<"PY"'}:
                continue
            invocations.append((Path(target).name.casefold(), args[args.index(target) + 1:]))
            continue
        invocations.append((executable, args))
    return invocations


def _classify_command(command: str) -> str | None:
    if not command:
        return None
    for target, args in _shell_invocations(command):
        if (target in {"pytest", "py.test", "ctest", "tox", "vitest", "jest", "unittest"}
                or re.match(r"(?:test_.+|.+_test)\.py$", target)
                or (target in {"go", "cargo"} and args[:1] == ["test"])
                or (target in {"npm", "pnpm", "yarn"}
                    and (args[:1] == ["test"] or args[:2] == ["run", "test"]))):
            return "test"
        runnable = " ".join([target, *args[:2]])
        if _BENCHMARK_RE.search(runnable):
            return "benchmark"
        if _EXPERIMENT_RE.search(target):
            return "experiment"
    return None


def _metric_items(text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in _METRIC_RE.finditer(text):
        name = re.sub(r"[ _]+", "_", match.group("name").lower())
        key = (name, match.group("value"), match.group("unit") or "")
        if key in seen:
            continue
        seen.add(key)
        output.append({"name": name, "value": float(match.group("value")),
                       "unit": match.group("unit") or None, "raw": match.group(0)[:160]})
        if len(output) >= 20:
            break
    return output


def _test_counts(text: str) -> dict[str, int]:
    # A Bash tool may chain ``pytest && another command``.  Only the first
    # pytest summary belongs to the test run; later prose may itself contain
    # phrases such as "152 passed" and must not replace the real count.
    for line in text.splitlines() or [text]:
        matches = list(_TEST_COUNT_RE.finditer(line))
        if not matches:
            continue
        output: dict[str, int] = {}
        for match in matches:
            key = match.group("status").lower()
            if key == "errors":
                key = "error"
            output[key] = int(match.group("count"))
        return output
    return {}


def _test_result_window(text: str) -> str:
    """Stop test parsing at the first terminal summary line.

    This keeps metrics printed by the test itself, while excluding stdout from
    subsequent shell commands in the same tool call.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _TEST_COUNT_RE.search(line):
            return "\n".join(lines[:index + 1])
    return text


def _parameters(command: str) -> dict[str, str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return {}
    output: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--") and len(token) > 2:
            if "=" in token:
                key, value = token[2:].split("=", 1)
            elif index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                key, value = token[2:], tokens[index + 1]
                index += 1
            else:
                key, value = token[2:], "true"
            if not re.search(r"(?:token|secret|password|api[-_]?key)", key, re.I):
                output[key] = redact_text(value)[:300]
        index += 1
    return dict(list(output.items())[:40])


def _run_name(command: str, kind: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    interesting = [Path(token).name for token in tokens
                   if token.endswith((".py", ".sh")) and re.search(r"train|eval|bench|infer|test|sweep", token, re.I)]
    if interesting:
        return interesting[-1]
    return {"test": "Agent 执行的测试", "benchmark": "Agent 执行的 Benchmark",
            "experiment": "Agent 执行的实验/评测"}[kind]


def _snap(home: Path, project_id: str | None) -> tuple[str | None, str | None, bool | None]:
    if not project_id:
        return None, None, None
    try:
        repo = Path(project_config(home, project_id)["repo_path"])
        value = snapshot(repo)
        return str(repo), value.get("commit_sha"), value.get("dirty")
    except (KeyError, ValueError, OSError):
        return None, None, None


def _incoming_paths(incoming: dict[str, Any]) -> list[str]:
    paths = _workspace_paths(incoming.get("tool_input"))
    tool_input = incoming.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("cwd", "workdir", "path", "file_path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.startswith("/"):
                paths.append(value)
    return paths


def _recent_project(ledger: Ledger, session_id: str, incoming: dict[str, Any]) -> str | None:
    """Reuse only nearby hook evidence, never an aggregate usage assignment.

    Codex sessions may live for days and cross several projects.  The former
    fallback selected *any* recent event, including a token-usage snapshot,
    which made one old project label stick to later lifecycle events.
    """
    turn_id = incoming.get("turn_id") or incoming.get("prompt_id")
    clauses = [
        "session_id=?",
        "project_id IS NOT NULL",
        "event_type IN ('agent_tool_completed','agent_tool_failed')",
        "event_id NOT IN (SELECT supersedes FROM events WHERE supersedes IS NOT NULL)",
    ]
    args: list[Any] = [session_id]
    if turn_id:
        clauses.append("json_extract(payload_json, '$.turn_id')=?")
        args.append(str(turn_id))
    row = ledger.db.execute(
        f"SELECT project_id,occurred_at FROM events WHERE {' AND '.join(clauses)} "
        "ORDER BY occurred_at DESC, ingested_at DESC LIMIT 1", args,
    ).fetchone()
    if not row:
        return None
    if turn_id:
        return str(row[0])
    try:
        observed = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        current = datetime.fromisoformat(_event_time(incoming).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if abs(current - observed) <= timedelta(minutes=10):
            return str(row[0])
    except ValueError:
        pass
    return None


def _resolve_project(home: Path, ledger: Ledger, session_id: str, incoming: dict[str, Any]) -> str | None:
    paths = _incoming_paths(incoming)
    topics = [str(value) for value in (
        incoming.get("session_title"), _command(incoming),
    ) if isinstance(value, str) and value.strip()]
    candidates: list[str] = []
    tool_input = incoming.get("tool_input")
    if isinstance(tool_input, dict):
        candidates.extend(str(tool_input[key]) for key in ("workdir", "cwd")
                          if isinstance(tool_input.get(key), str))
    if isinstance(incoming.get("cwd"), str):
        candidates.append(str(incoming["cwd"]))
    for cwd in candidates:
        project = _project_for_cwd(home, cwd, topics=topics, observed_paths=paths)
        if project:
            return project
    if incoming.get("hook_event_name") == "SessionEnd":
        return _recent_project(ledger, session_id, incoming)
    return None


def _ensure_start(home: Path, ledger: Ledger, source: str, incoming: dict[str, Any],
                  project_id: str | None) -> str:
    session_id = str(incoming.get("session_id") or "")
    if not session_id:
        raise ValueError("agent hook requires session_id")
    repo_path, commit_sha, dirty = _snap(home, project_id)
    config = load_config(home / "config" / "projects.yaml")
    project_name = ((config.get("projects") or {}).get(project_id or "") or {}).get("name")
    title = incoming.get("session_title")
    goal = str(title or (f"继续 {project_name}" if project_name else "Agent 会话（项目待自动识别）"))
    return ledger.append(
        event_type="agent_session_started", source=source, project_id=project_id,
        session_id=session_id, machine=machine_name(home), repo_path=repo_path,
        commit_sha=commit_sha, dirty=dirty, status="active", provenance="observed",
        payload={"goal": redact_text(goal)[:500], "goal_source": "session_title" if title else "lifecycle",
                 "start_source": incoming.get("source"), "model": incoming.get("model")},
        evidence=_evidence(incoming),
        # A long-lived Agent session can legitimately switch projects.  Treat
        # each newly observed project as a new resumable segment.
        dedup_key=f"agent-hook:{source}:{session_id}:start:{project_id or 'unassigned'}",
        occurred_at=_event_time(incoming),
    )


def _record_tool(home: Path, ledger: Ledger, source: str, incoming: dict[str, Any],
                 project_id: str | None) -> dict[str, Any]:
    session_id = str(incoming["session_id"])
    command = redact_text(_command(incoming))
    response = _response_text(incoming)
    failed = _failed(incoming, response)
    kind = _classify_command(command)
    tool_id = str(incoming.get("tool_use_id") or hashlib.sha256(
        f"{session_id}:{command}:{_event_time(incoming)}".encode()).hexdigest()[:20])
    repo_path, commit_sha, dirty = _snap(home, project_id)
    compact_response = response[-2400:] if response else ""
    observed_id = ledger.append(
        event_type="agent_tool_failed" if failed else "agent_tool_completed", source=source,
        project_id=project_id, session_id=session_id, machine=machine_name(home),
        repo_path=repo_path, commit_sha=commit_sha, dirty=dirty,
        status="failed" if failed else "passed", provenance="observed",
        payload={"tool_name": incoming.get("tool_name"), "tool_use_id": tool_id,
                 "turn_id": incoming.get("turn_id") or incoming.get("prompt_id"),
                 "command": command[:4000], "duration_ms": incoming.get("duration_ms"),
                 "response_excerpt": compact_response, "semantic_kind": kind},
        evidence=_evidence(incoming),
        dedup_key=f"agent-hook:{source}:{session_id}:tool:{tool_id}",
        occurred_at=_event_time(incoming),
    )
    if not kind or not project_id or re.search(r"(?:^|\s)rd\s+run(?:\s|$)", command):
        return {"observed_event": observed_id, "classified": kind, "project_id": project_id}

    parameters = _parameters(command)
    semantic_response = _test_result_window(response) if kind == "test" else response
    metrics = _metric_items(semantic_response)
    counts = _test_counts(semantic_response) if kind == "test" else {}
    fingerprint = hashlib.sha256(re.sub(r"\s+", " ", command.strip()).encode()).hexdigest()
    semantic_type = (("test_failed" if failed else "test_completed") if kind == "test" else
                     ("experiment_failed" if failed else
                      "benchmark_completed" if kind == "benchmark" else "experiment_completed"))
    experiment_id = f"agentexp_{hashlib.sha256(f'{source}:{session_id}:{tool_id}'.encode()).hexdigest()[:16]}"
    payload = {
        "experiment_id": experiment_id, "name": _run_name(command, kind), "kind": kind,
        "command": command[:4000], "parameters": parameters,
        "dataset": parameters.get("dataset") or parameters.get("data") or parameters.get("dataset_path"),
        "model": parameters.get("model") or parameters.get("model_path") or parameters.get("checkpoint"),
        "metrics": {item["name"]: item["value"] for item in metrics}, "metric_items": metrics,
        "test_counts": counts, "result": semantic_response[-1200:],
        "fingerprint": fingerprint, "supports": [observed_id],
        "extraction": {"method": "agent_tool_rule", "confidence": "inferred",
                       "host_event": incoming.get("hook_event_name")},
    }
    semantic_id = ledger.append(
        event_type=semantic_type, source=source, project_id=project_id, session_id=session_id,
        machine=machine_name(home), repo_path=repo_path, commit_sha=commit_sha, dirty=dirty,
        status="failed" if failed else "passed", provenance="inferred", payload=payload,
        evidence=[*_evidence(incoming), {"type": "event_ref", "path": f"event:{observed_id}"}],
        dedup_key=f"agent-hook:{source}:{session_id}:semantic:{tool_id}:{kind}",
        occurred_at=_event_time(incoming),
    )
    metric_ids = []
    for item in metrics:
        metric_ids.append(ledger.append(
            event_type="metric_observed", source=source, project_id=project_id, session_id=session_id,
            machine=machine_name(home), repo_path=repo_path, commit_sha=commit_sha, dirty=dirty,
            status="observed", provenance="observed",
            payload={**item, "experiment_id": experiment_id, "source_event": semantic_id},
            evidence=[{"type": "event_ref", "path": f"event:{observed_id}"}],
            dedup_key=f"agent-hook:{source}:{session_id}:metric:{tool_id}:{item['name']}:{item['value']}",
            occurred_at=_event_time(incoming),
        ))
    return {"observed_event": observed_id, "semantic_event": semantic_id,
            "metric_events": metric_ids, "classified": kind, "project_id": project_id}


def handle_agent_hook(home: Path, ledger: Ledger, source: str,
                      incoming: dict[str, Any]) -> dict[str, Any]:
    """Normalize one official host hook payload and append only compact facts."""
    session_id = str(incoming.get("session_id") or "")
    event_name = str(incoming.get("hook_event_name") or "")
    if not session_id or not event_name:
        raise ValueError("agent hook requires session_id and hook_event_name")
    if event_name not in {"SessionStart", "PostToolUse", "PostToolUseFailure", "SessionEnd"}:
        return {"accepted": True, "ignored_event": event_name,
                "reason": "unsupported lifecycle event"}
    project_id = _resolve_project(home, ledger, session_id, incoming)
    start_id = _ensure_start(home, ledger, source, incoming, project_id)

    if event_name in {"PostToolUse", "PostToolUseFailure"}:
        return {"accepted": True, "start_event": start_id,
                **_record_tool(home, ledger, source, incoming, project_id)}

    if event_name == "SessionEnd":
        repo_path, commit_sha, dirty = _snap(home, project_id)
        eid = ledger.append(
            event_type="agent_session_completed", source=source, project_id=project_id,
            session_id=session_id, machine=machine_name(home), repo_path=repo_path,
            commit_sha=commit_sha, dirty=dirty, status="completed", provenance="observed",
            payload={"reason": incoming.get("reason"), "summary": None},
            evidence=_evidence(incoming), dedup_key=f"agent-hook:{source}:{session_id}:end",
            occurred_at=_event_time(incoming),
        )
        return {"accepted": True, "project_id": project_id, "event_id": eid}

    # SessionStart is fully represented by _ensure_start.  Unknown lifecycle
    # events are acknowledged so adding the hook never interferes with an agent.
    return {"accepted": True, "project_id": project_id, "event_id": start_id,
            "ignored_event": None if event_name == "SessionStart" else event_name}
