from __future__ import annotations

import json
import subprocess
import urllib.error

import pytest

from rd_cockpit.llm import enrich_semantic


class _Response:
    def __init__(self, value: dict): self.value = value
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.value).encode()


def test_llm_requires_configuration(monkeypatch):
    monkeypatch.setenv("RD_LLM_MODEL", "")
    monkeypatch.setenv("RD_LLM_FALLBACK_MODEL", "")
    with pytest.raises(RuntimeError, match="requires"):
        enrich_semantic({"today_results": []})


def test_llm_rejects_unverifiable_evidence(monkeypatch):
    monkeypatch.setenv("RD_LLM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("RD_LLM_MODEL", "test")
    monkeypatch.setenv("RD_LLM_FALLBACK_MODEL", "")
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _Response({
        "choices": [{"message": {"content": '{"today_results":[{"text":"invented","evidence":["evt_missing"]}]}'}}]
    }))
    with pytest.raises(RuntimeError, match="not present"):
        enrich_semantic({"today_results": [{"text": "real", "evidence": ["evt_real"]}]})


def test_llm_falls_back_after_primary_request_failure(monkeypatch):
    monkeypatch.setenv("RD_LLM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("RD_LLM_MODEL", "deepseek-local")
    monkeypatch.setenv("RD_LLM_FALLBACK_MODEL", "deepseek")
    calls: list[str] = []

    def fake_urlopen(request, **kwargs):
        model = json.loads(request.data)["model"]
        calls.append(model)
        if model == "deepseek-local":
            raise urllib.error.URLError("local unavailable")
        return _Response({"choices": [{"message": {"content": '{"today_results":[]}'}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = enrich_semantic({"today_results": []})

    assert calls == ["deepseek-local", "deepseek"]
    assert result["model"] == "deepseek"
    assert result["fallback_used"] is True
    assert result["attempted_models"] == calls


def test_llm_falls_back_after_primary_validation_failure(monkeypatch):
    monkeypatch.setenv("RD_LLM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setenv("RD_LLM_MODEL", "deepseek-local")
    monkeypatch.setenv("RD_LLM_FALLBACK_MODEL", "deepseek")
    responses = iter([
        _Response({"choices": [{"message": {"content": "not-json"}}]}),
        _Response({"choices": [{"message": {"content": '{"current_blockers":[]}'}}]}),
    ])
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: next(responses))

    result = enrich_semantic({"today_results": []})

    assert result["model"] == "deepseek"
    assert result["fallback_used"] is True


def test_llm_uses_claude_cli_router_by_default(monkeypatch):
    monkeypatch.delenv("RD_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("RD_LLM_MODEL", "deepseek-local")
    monkeypatch.setenv("RD_LLM_FALLBACK_MODEL", "deepseek")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0,
            stdout=json.dumps({"result": '{"today_results":[]}', "usage": {"input_tokens": 12}}),
            stderr="",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    result = enrich_semantic({"today_results": []})

    assert calls[0][calls[0].index("--model") + 1] == "deepseek-local"
    assert result["model"] == "deepseek-local"
    assert result["fallback_used"] is False
