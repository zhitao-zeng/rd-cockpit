"""One bounded subprocess transport for Codex and Claude JSON analyzers.

Prompt construction and evidence validation stay in their domain modules.
This module owns the repetitive and failure-prone lifecycle details: executable
resolution, timeouts, an isolated working directory, structured output parsing
and usage metadata.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pathlib import Path
from typing import Any

from .runtime import executable as resolve_executable
from .model_runs import record_model_run


def json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("model output does not contain a JSON object")


def _run_codex_json(
    model_spec: str,
    instruction: dict[str, Any],
    *,
    prompt: str,
    executable_env: str,
    timeout_env: str,
    default_timeout: float,
    reasoning_env: str | None = None,
    workdir: Path | None = None,
    temp_prefix: str = "rd-model-",
) -> tuple[dict[str, Any], dict[str, Any]]:
    model_and_effort = model_spec.removeprefix("codex:")
    model, separator, reasoning = model_and_effort.partition("@")
    if not separator:
        reasoning = os.environ.get(reasoning_env, "medium") if reasoning_env else "medium"
    executable = resolve_executable(executable_env, "codex")
    timeout = float(os.environ.get(timeout_env, str(default_timeout)))
    with tempfile.TemporaryDirectory(prefix=temp_prefix) as temporary:
        isolated = Path(temporary)
        message = isolated / "last-message.json"
        command = [
            executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only", "--model", model,
            "-c", 'model_provider="openai"', "-c", f'model_reasoning_effort="{reasoning}"',
            "-C", str(workdir or isolated), "--json", "--output-last-message", str(message), prompt,
        ]
        try:
            completed = subprocess.run(
                command, input=json.dumps(instruction, ensure_ascii=False), capture_output=True,
                text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Codex timed out after {timeout:g}s") from exc
        except OSError as exc:
            raise RuntimeError(f"could not start Codex: {exc}") from exc
        if completed.returncode:
            detail = completed.stderr.strip().splitlines()
            suffix = f": {detail[-1][:500]}" if detail else ""
            raise RuntimeError(f"Codex exited with {completed.returncode}{suffix}")
        try:
            result = json_object(message.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"invalid Codex structured output: {exc}") from exc
        usage: dict[str, Any] = {}
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
    return result, {"model": model_spec, "provider": "codex-cli",
                    "reasoning_effort": reasoning, "usage": usage}


def run_codex_json(
    model_spec: str,
    instruction: dict[str, Any],
    *,
    prompt: str,
    executable_env: str,
    timeout_env: str,
    default_timeout: float,
    reasoning_env: str | None = None,
    workdir: Path | None = None,
    temp_prefix: str = "rd-model-",
    run_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = datetime.now(timezone.utc)
    try:
        result, metadata = _run_codex_json(
            model_spec, instruction, prompt=prompt, executable_env=executable_env,
            timeout_env=timeout_env, default_timeout=default_timeout,
            reasoning_env=reasoning_env, workdir=workdir, temp_prefix=temp_prefix,
        )
    except Exception as exc:
        record_model_run(
            run_context, requested_model=model_spec, status="failed", started_at=started,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    record_model_run(
        run_context, requested_model=model_spec, metadata=metadata, status="ok", started_at=started,
    )
    return result, metadata


def _run_claude_json(
    model: str,
    instruction: dict[str, Any],
    *,
    prompt: str,
    executable_env: str,
    timeout_env: str,
    default_timeout: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if model.casefold().startswith("deepseek") or os.environ.get("RD_MODEL_ROUTER_URL"):
        return _run_anthropic_json(
            model, instruction, prompt=prompt, timeout_env=timeout_env,
            default_timeout=default_timeout,
        )
    executable = resolve_executable(executable_env, "claude")
    timeout = float(os.environ.get(timeout_env, str(default_timeout)))
    command = [executable, "-p", prompt, "--model", model, "--tools", "",
               "--disable-slash-commands", "--no-session-persistence", "--output-format", "json"]
    try:
        completed = subprocess.run(
            command, input=json.dumps(instruction, ensure_ascii=False), capture_output=True,
            text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Claude timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start Claude: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip().splitlines()
        suffix = f": {detail[-1][:500]}" if detail else ""
        raise RuntimeError(f"Claude exited with {completed.returncode}{suffix}")
    outer = json_object(completed.stdout)
    value = outer.get("structured_output", outer.get("result", outer))
    result = value if isinstance(value, dict) else json_object(str(value))
    usage = outer.get("usage") if isinstance(outer.get("usage"), dict) else {}
    return result, {"model": model, "provider": "claude-cli", "usage": usage}


def run_claude_json(
    model: str,
    instruction: dict[str, Any],
    *,
    prompt: str,
    executable_env: str,
    timeout_env: str,
    default_timeout: float,
    run_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = datetime.now(timezone.utc)
    try:
        result, metadata = _run_claude_json(
            model, instruction, prompt=prompt, executable_env=executable_env,
            timeout_env=timeout_env, default_timeout=default_timeout,
        )
    except Exception as exc:
        record_model_run(
            run_context, requested_model=model, status="failed", started_at=started,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    record_model_run(
        run_context, requested_model=model, metadata=metadata, status="ok", started_at=started,
    )
    return result, metadata


def _run_anthropic_json(
    model: str,
    instruction: dict[str, Any],
    *,
    prompt: str,
    timeout_env: str,
    default_timeout: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call the local Anthropic-compatible router used by DeepSeek aliases."""
    endpoint = os.environ.get("RD_MODEL_ROUTER_URL", "http://127.0.0.1:4000/v1/messages")
    timeout = float(os.environ.get(timeout_env, str(default_timeout)))
    payload = {
        "model": model,
        "max_tokens": min(24000, max(4000, len(json.dumps(instruction, ensure_ascii=False)) // 3)),
        "temperature": 0,
        "stream": False,
        "system": prompt,
        "messages": [{"role": "user", "content": json.dumps(instruction, ensure_ascii=False)}],
    }
    headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    token = os.environ.get("RD_MODEL_ROUTER_API_KEY")
    headers["x-api-key"] = token or "local-router"
    request = Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost by default
            outer = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"local model router failed: {type(exc).__name__}: {exc}") from exc
    content = outer.get("content") if isinstance(outer, dict) else None
    text = "".join(
        str(block.get("text") or "") for block in content or []
        if isinstance(block, dict) and block.get("type") == "text"
    )
    result = json_object(text)
    usage = outer.get("usage") if isinstance(outer, dict) and isinstance(outer.get("usage"), dict) else {}
    return result, {"model": model, "provider": "anthropic-compatible", "usage": usage}


def run_anthropic_json(
    model: str,
    instruction: dict[str, Any],
    *,
    prompt: str,
    timeout_env: str,
    default_timeout: float,
    run_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = datetime.now(timezone.utc)
    try:
        result, metadata = _run_anthropic_json(
            model, instruction, prompt=prompt, timeout_env=timeout_env,
            default_timeout=default_timeout,
        )
    except Exception as exc:
        record_model_run(
            run_context, requested_model=model, status="failed", started_at=started,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    record_model_run(
        run_context, requested_model=model, metadata=metadata, status="ok", started_at=started,
    )
    return result, metadata
