from rd_cockpit.security import redact_text


def test_common_credentials_are_redacted() -> None:
    value = "curl --api-key=abc123 --token secret Bearer xyz https://user:pass@example.com"
    result = redact_text(value)
    assert "abc123" not in result
    assert "secret" not in result
    assert "xyz" not in result
    assert "user:pass" not in result
    assert "[REDACTED]" in result


def test_yaml_json_secrets_are_redacted_without_corrupting_model_prose() -> None:
    value = 'token: private\n{"api_key":"also-private"}\nglobal token: spatial context\ntoken-to-spatial attention'
    result = redact_text(value)
    assert "private" not in result
    assert "also-private" not in result
    assert "global token: spatial context" in result
    assert "token-to-spatial attention" in result
