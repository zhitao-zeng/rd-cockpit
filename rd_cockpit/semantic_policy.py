"""Stable cache identities for model-derived semantic artifacts.

Source hashes alone are insufficient: changing a prompt, output schema,
project catalog, or model policy changes the meaning of an otherwise
byte-identical source.  This module gives every semantic cache a small,
secret-free content fingerprint over those inputs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


POLICY_FORMAT_VERSION = 1


def policy_fingerprint(
    stage: str,
    *,
    schema_version: int,
    prompt_version: int,
    models: Iterable[str | None] = (),
    extra: dict[str, Any] | None = None,
) -> str:
    payload = {
        "format": POLICY_FORMAT_VERSION,
        "stage": stage,
        "schema_version": schema_version,
        "prompt_version": prompt_version,
        "models": [str(model).strip() for model in models if model and str(model).strip()],
        "extra": extra or {},
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def catalog_fingerprint(catalog: dict[str, str]) -> str:
    encoded = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
