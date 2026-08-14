import json
from pathlib import Path

from rd_cockpit.model_runner import run_claude_json
from rd_cockpit.model_runs import model_run_summary


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps({
            "content": [{"type": "text", "text": '{"answer":"ok"}'}],
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }).encode()


def test_deepseek_alias_uses_local_router_without_claude_binary(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("rd_cockpit.model_runner.urlopen", fake_urlopen)
    result, metadata = run_claude_json(
        "deepseek-local", {"evidence": ["fact-1"]}, prompt="JSON only",
        executable_env="RD_TEST_CLAUDE_BIN", timeout_env="RD_TEST_TIMEOUT",
        default_timeout=30,
    )

    assert result == {"answer": "ok"}
    assert metadata["provider"] == "anthropic-compatible"
    assert captured["url"] == "http://127.0.0.1:4000/v1/messages"
    assert captured["payload"]["model"] == "deepseek-local"


def test_model_call_accounting_stores_metadata_but_not_prompt(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr("rd_cockpit.model_runner.urlopen", lambda *_args, **_kwargs: _Response())
    run_claude_json(
        "deepseek-local", {"private": "must-not-be-stored"}, prompt="secret prompt",
        executable_env="RD_TEST_CLAUDE_BIN", timeout_env="RD_TEST_TIMEOUT", default_timeout=30,
        run_context={"home": tmp_path, "stage": "architecture", "project_id": "demo",
                     "source_hash": "abc", "reason": "source changed"},
    )

    value = model_run_summary(tmp_path, days=1)
    assert value["counts"]["model_calls"] == 1
    assert value["tokens"]["total"] == 15
    assert value["runs"][0]["project_id"] == "demo"
    assert "secret" not in json.dumps(value)
