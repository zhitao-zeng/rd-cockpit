"""Optional semantic enrichment through Claude CLI or OpenAI-compatible HTTP."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import hashlib
from datetime import datetime, timezone
from typing import Any

from .runtime import executable as resolve_executable
from .model_runs import record_model_run


def _event_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("event_id"), str): found.add(value["event_id"])
        evidence = value.get("evidence")
        if isinstance(evidence, list): found.update(item for item in evidence if isinstance(item, str))
        for item in value.values(): found.update(_event_ids(item))
    elif isinstance(value, list):
        for item in value: found.update(_event_ids(item))
    return found


def _json_content(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    value = json.loads(text)
    if not isinstance(value, dict): raise ValueError("LLM response must be a JSON object")
    return value


def _configured_models() -> list[str]:
    primary = os.environ.get("RD_LLM_MODEL", "deepseek-local").strip()
    fallback = os.environ.get("RD_LLM_FALLBACK_MODEL", "deepseek").strip()
    return list(dict.fromkeys(model for model in (primary, fallback) if model))


def _system_prompt() -> str:
    return (
        "You summarize a research ledger. Return JSON only with keys "
        "today_results, yesterday_plan_closure, current_blockers, next_actions. "
        "Do not add facts. Every item must preserve an evidence list containing only IDs from the input. "
        "Mark uncertainty in wording; never invent metrics or dates."
    )


def _validate_result(result: dict[str, Any], evidence_ids: set[str]) -> dict[str, Any]:
    allowed = {"today_results", "yesterday_plan_closure", "current_blockers", "next_actions"}
    if set(result) - allowed:
        raise RuntimeError(f"unsupported fields: {sorted(set(result) - allowed)}")
    returned_ids = _event_ids(result)
    if not returned_ids.issubset(evidence_ids):
        raise RuntimeError("cited an event that was not present in the candidate ledger facts")
    return result


def _request_openai_model(
    endpoint: str,
    model: str,
    semantic: dict[str, Any],
    *,
    timeout: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = json.dumps({"model": model, "temperature": 0,
                       "messages": [{"role": "system", "content": _system_prompt()},
                                    {"role": "user", "content": json.dumps(semantic, ensure_ascii=False)}]},
                      ensure_ascii=False).encode()
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("RD_LLM_API_KEY")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            outer = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"request failed: {exc}") from exc
    try:
        content = outer["choices"][0]["message"]["content"]
        result = _json_content(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"invalid structured output: {exc}") from exc
    usage = outer.get("usage") if isinstance(outer.get("usage"), dict) else {}
    return result, {"model": model, "provider": "openai-compatible", "usage": usage}


def _request_claude_model(
    model: str, semantic: dict[str, Any], *, timeout: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    executable = resolve_executable("RD_LLM_CLAUDE_BIN", "claude")
    command = [
        executable, "-p", _system_prompt(), "--model", model, "--tools", "",
        "--disable-slash-commands", "--no-session-persistence", "--output-format", "json",
    ]
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(semantic, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"request timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start Claude CLI: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()
        suffix = f": {detail[-1][:240]}" if detail else ""
        raise RuntimeError(f"Claude CLI exited with {completed.returncode}{suffix}")
    try:
        outer = json.loads(completed.stdout)
        content = outer.get("structured_output", outer.get("result", outer))
        result = content if isinstance(content, dict) else _json_content(str(content))
        usage = outer.get("usage") if isinstance(outer.get("usage"), dict) else {}
        return result, {"model": model, "provider": "claude-cli", "usage": usage}
    except (AttributeError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"invalid structured output: {exc}") from exc


def enrich_semantic(semantic: dict[str, Any], *, timeout: float = 45.0) -> dict[str, Any]:
    base_url = os.environ.get("RD_LLM_BASE_URL")
    models = _configured_models()
    if not models:
        raise RuntimeError("LLM enrichment requires at least one configured model")
    endpoint = base_url.rstrip("/") if base_url else None
    if endpoint and not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    evidence_ids = _event_ids(semantic)
    source_hash = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    errors: list[str] = []
    for index, model in enumerate(models):
        started = datetime.now(timezone.utc)
        metadata: dict[str, Any] = {}
        try:
            if endpoint:
                result, metadata = _request_openai_model(endpoint, model, semantic, timeout=timeout)
            else:
                result, metadata = _request_claude_model(model, semantic, timeout=timeout)
            result = _validate_result(result, evidence_ids)
        except RuntimeError as exc:
            record_model_run(
                {"home": os.environ.get("RD_COCKPIT_HOME"), "stage": "legacy_semantic",
                 "source_hash": source_hash, "fallback_used": index > 0,
                 "reason": "显式请求了旧版事件账本语义摘要。"},
                requested_model=model, metadata=metadata, status="failed", started_at=started,
                error=f"{type(exc).__name__}: {exc}",
            )
            errors.append(f"{model}: {exc}")
            continue
        record_model_run(
            {"home": os.environ.get("RD_COCKPIT_HOME"), "stage": "legacy_semantic",
             "source_hash": source_hash, "fallback_used": index > 0,
             "reason": "显式请求了旧版事件账本语义摘要。"},
            requested_model=model, metadata=metadata, status="ok", started_at=started,
        )
        return {
            "model": model,
            "primary_model": models[0],
            "fallback_used": index > 0,
            "attempted_models": models[:index + 1],
            "verified": True,
            "summary": result,
        }
    raise RuntimeError(f"all LLM models failed ({'; '.join(errors)})")
