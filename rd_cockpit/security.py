from __future__ import annotations

import re
from typing import Any


_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(--?(?:api[-_]?key|token|password|secret)(?:=|\s+))[^ \s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|token|password|secret)=)[^&\s]+"), r"\1[REDACTED]"),
    # YAML-style secret assignments.  Anchor at line start so model source
    # prose such as ``global token: spatial context`` is not corrupted.
    (re.compile(r"(?im)^(\s*[\"']?(?:api[_-]?key|token|password|secret)[\"']?\s*:\s*)[\"']?[^\s,}\"']+"),
     r"\1[REDACTED]"),
    # Compact JSON assignments can occur after an opening brace or comma.
    (re.compile(r"(?i)([,{]\s*[\"'](?:api[_-]?key|token|password|secret)[\"']\s*:\s*)[\"']?[^\s,}\"']+"),
     r"\1[REDACTED]"),
    (re.compile(r"https?://[^\s/@:]+:[^\s/@]+@"), "https://[REDACTED]@"),
]


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any) -> Any:
    if isinstance(value, str): return redact_text(value)
    if isinstance(value, list): return [redact_value(item) for item in value]
    if isinstance(value, dict): return {key: redact_value(item) for key, item in value.items()}
    return value
