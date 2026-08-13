"""Evidence-grounded, versioned algorithm architecture snapshots.

The source repository and audited Daily Reports remain authoritative.  This
module builds a bounded evidence bundle, asks an optional model to explain the
algorithm in a shared schema, validates every source reference, and stores a
regenerable snapshot under ``.rd-cockpit``.  The read-only API never invokes a
model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .config import load_config, project_config
from .daily_source import report_directories
from .model_evidence import evidence_for_project
from .security import redact_text
from .runtime import executable as resolve_executable


SCHEMA_VERSION = 2
DEFAULT_MODEL = "codex:gpt-5.6-sol@medium"
DEFAULT_FALLBACK_MODEL = "deepseek-local"
MAX_DISCOVERED_FILES = 30_000
MAX_PRESELECTED_FILES = 240
MAX_SELECTED_FILES = 48
MAX_FILE_BYTES = 320_000
MAX_BUNDLE_BYTES = 220_000
MAX_EVIDENCE_ITEMS = 140
MAX_EXTERNAL_EVIDENCE_ITEMS = 28
MAX_REPORT_EVIDENCE_ITEMS = 30

SOURCE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SOURCE_REF_RE = re.compile(r"^source:([a-z][a-z0-9_]*):(.+):L(\d+)-L(\d+)$")
REPORT_REF_RE = re.compile(r"^report:(\d{4}-\d{2}-\d{2}):L(\d+)-L(\d+)$")
NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:\.\d+)?%?")

TEXT_SUFFIXES = {
    ".md", ".rst", ".txt", ".py", ".yaml", ".yml", ".json", ".toml",
    ".ini", ".cfg", ".sh", ".cpp", ".cc", ".c", ".h", ".hpp", ".rs",
    ".go", ".java", ".ts", ".tsx",
}
IGNORED_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "target", ".next", ".cache",
    "checkpoints", "wandb", "runs", "outputs", "tmp", "temp",
}
PATH_TERMS = (
    "readme", "architecture", "algorithm", "model", "pipeline", "config", "train",
    "infer", "predict", "evaluate", "evaluation", "benchmark", "metric", "result",
    "experiment", "ablation", "decoder", "encoder", "backbone", "head", "loss",
)
CONTENT_TERMS = (
    "architecture", "algorithm", "pipeline", "model", "backbone", "encoder", "decoder",
    "neck", "head", "forward", "inference", "training", "loss", "metric", "benchmark",
    "baseline", "ablation", "checkpoint", "quantization", "int8", "fp16", "onnx",
    "tensorrt", "input", "output", "threshold", "模型", "算法", "架构", "训练", "推理",
    "评测", "指标", "基线", "消融", "量化", "输入", "输出", "阈值",
)
SECRET_NAME_RE = re.compile(r"(?i)(secret|token|credential|password|api[_-]?key|cookie)")

NODE_CATEGORIES = {
    "input", "preprocess", "router", "model", "fusion", "postprocess", "decision", "output",
}
NODE_STATUSES = {"current", "candidate", "optional", "legacy", "rejected", "unknown"}
MODEL_ARCH_STATUSES = {"verified", "partial", "opaque"}
MODEL_ARCH_BASES = {"deployment_evidence", "family_reference", "mixed", "undisclosed"}
SNAPSHOT_STATUSES = {"ready", "insufficient_evidence", "analysis_failed"}
DECISION_STATUSES = {"adopted", "conditional", "candidate", "rejected", "superseded", "unknown"}
DIFF_KINDS = {"added", "removed", "changed", "warning"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output must be one JSON object")
    return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _text(value: Any, limit: int = 8_000) -> str:
    return redact_text(str(value or "").strip())[:limit]


def _identifier(value: Any, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(value or "").casefold()).strip("_")
    return (normalized or fallback)[:80]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_state(repo: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], text=True, capture_output=True,
            timeout=15, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    head = run("rev-parse", "HEAD")
    branch = run("branch", "--show-current")
    status = run("status", "--porcelain", "--untracked-files=no") if head else ""
    return {"head": head or None, "branch": branch or None, "dirty": bool(status) if head else None}


def _configured_sources(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    repo = Path(str(config.get("repo_path") or "")).expanduser().resolve()
    output = [{"id": "repo", "label": "项目仓库", "root": repo,
               "focus_files": _strings(config.get("algorithm_focus_files"))}]
    for index, item in enumerate(config.get("algorithm_sources") or []):
        if isinstance(item, str):
            source_id, path, label, focus_files = (
                f"extra_{index + 1}", item, f"补充来源 {index + 1}", [],
            )
        elif isinstance(item, Mapping):
            source_id = str(item.get("id") or f"extra_{index + 1}")
            path = str(item.get("path") or "")
            label = str(item.get("label") or source_id)
            focus_files = _strings(item.get("focus_files"))
        else:
            continue
        if not SOURCE_ID_RE.fullmatch(source_id) or source_id == "repo" or not path:
            continue
        root = Path(path).expanduser().resolve()
        if any(root == existing["root"] for existing in output):
            continue
        output.append({"id": source_id, "label": label, "root": root,
                       "focus_files": focus_files})
    return output


def _path_score(relative: Path) -> int:
    lowered = relative.as_posix().casefold()
    name = relative.name.casefold()
    score = 0
    if name.startswith("readme"):
        score += 120
    if name in {"config.yaml", "config.yml", "pyproject.toml", "package.json"}:
        score += 80
    score += min(120, 18 * sum(term in lowered for term in PATH_TERMS))
    if "/docs/" in f"/{lowered}" or lowered.startswith("docs/"):
        score += 18
    if "/results/" in f"/{lowered}" or lowered.startswith("results/"):
        score += 22
    if relative.suffix.casefold() in {".yaml", ".yml", ".json"}:
        score += 12
    return score


def _candidate_files(root: Path) -> list[tuple[int, Path]]:
    if not root.is_dir():
        return []
    root = root.resolve()
    candidates: list[tuple[int, Path]] = []
    discovered = 0
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(name for name in names if name not in IGNORED_DIRS and not name.startswith("."))
        base = Path(directory)
        for name in sorted(files):
            discovered += 1
            if discovered > MAX_DISCOVERED_FILES:
                break
            path = base / name
            try:
                path.resolve().relative_to(root)
            except (OSError, ValueError):
                # Do not follow a project-local symlink into an unrelated
                # model, dataset, credential or workspace tree.
                continue
            suffix = path.suffix.casefold()
            if suffix not in TEXT_SUFFIXES or SECRET_NAME_RE.search(name):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if not 0 < size <= MAX_FILE_BYTES:
                continue
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            score = _path_score(relative)
            if score:
                candidates.append((score, path))
        if discovered > MAX_DISCOVERED_FILES:
            break
    candidates.sort(key=lambda item: (-item[0], item[1].as_posix()))
    return candidates[:MAX_PRESELECTED_FILES]


def _read_lines(path: Path) -> list[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return []
    if b"\x00" in raw[:4096]:
        return []
    try:
        return redact_text(raw.decode("utf-8", errors="replace")).splitlines()
    except Exception:
        return []


def _content_score(lines: list[str]) -> int:
    sample = "\n".join(lines[:2_000]).casefold()
    return min(180, sum(min(sample.count(term), 8) for term in CONTENT_TERMS) * 2)


def _merge_ranges(ranges: Iterable[tuple[int, int]], line_count: int) -> list[tuple[int, int]]:
    ordered = sorted((max(1, start), min(line_count, end)) for start, end in ranges if start <= end)
    output: list[tuple[int, int]] = []
    for start, end in ordered:
        if output and start <= output[-1][1] + 2:
            output[-1] = (output[-1][0], max(output[-1][1], end))
        else:
            output.append((start, end))
    return output


def _snippet_ranges(path: Path, lines: list[str]) -> list[tuple[int, int]]:
    count = len(lines)
    if not count:
        return []
    lowered_name = path.name.casefold()
    if count <= 140:
        return [(1, count)]
    matches = []
    for index, line in enumerate(lines, 1):
        lowered = line.casefold()
        if any(term in lowered for term in CONTENT_TERMS) or re.match(
            r"\s*(class|def)\s+.*(model|encoder|decoder|backbone|head|pipeline|loss|router|estimator)",
            lowered,
        ):
            matches.append(index)
    ranges: list[tuple[int, int]] = []
    if lowered_name.startswith("readme"):
        ranges.append((1, 55))
    elif "config" in lowered_name or path.suffix.casefold() in {".yaml", ".yml", ".toml"}:
        ranges.append((1, min(count, 180)))
    for index in matches[:18]:
        ranges.append((index - 9, index + 18))
    return _merge_ranges(ranges, count)[:12]


def _source_evidence(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    root = Path(source["root"])
    selected: list[tuple[int, Path, list[str]]] = []
    focused: set[Path] = set()
    for relative_text in _strings(source.get("focus_files")):
        candidate = (root / relative_text).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if not candidate.is_file() or candidate.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        lines = _read_lines(candidate)
        if lines:
            focused.add(candidate)
            selected.append((10_000, candidate, lines))
    for path_score, path in _candidate_files(root):
        if path.resolve() in focused:
            continue
        lines = _read_lines(path)
        if not lines:
            continue
        selected.append((path_score + _content_score(lines), path, lines))
    selected.sort(key=lambda item: (-item[0], item[1].as_posix()))
    evidence: list[dict[str, Any]] = []
    total_bytes = 0
    for _, path, lines in selected[:MAX_SELECTED_FILES]:
        relative = path.relative_to(root).as_posix()
        file_hash = _sha256(path)
        for start, end in _snippet_ranges(path, lines):
            text = "\n".join(lines[start - 1 : end]).strip()
            if not text:
                continue
            encoded = len(text.encode("utf-8"))
            if total_bytes + encoded > MAX_BUNDLE_BYTES or len(evidence) >= MAX_EVIDENCE_ITEMS:
                return evidence
            evidence.append({
                "ref": f"source:{source['id']}:{relative}:L{start}-L{end}",
                "source_id": source["id"], "path": relative, "line_start": start,
                "line_end": end, "sha256": file_hash, "kind": "source", "text": text,
            })
            total_bytes += encoded
    return evidence


def _report_path(day: str) -> Path | None:
    for root in report_directories():
        path = root / f"{day}.md"
        if path.is_file():
            return path
    return None


def _report_evidence(
    home: Path, project_id: str, *, days: int = 180,
    intelligence: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    try:
        if intelligence is None:
            from .intelligence import project_intelligence

            intelligence = project_intelligence(home, days=days, target=date.today())
        detail = intelligence.get("project_details", {}).get(project_id, {})
        refs: list[str] = []
        for item in detail.get("breakthroughs", []):
            refs.extend(_strings(item.get("evidence")))
        for item in detail.get("unknowns", []):
            refs.extend(_strings(item.get("evidence")))
        refs.extend(_strings((detail.get("storyline") or {}).get("evidence")))
    except Exception:
        return []
    output = []
    for ref in dict.fromkeys(refs):
        match = REPORT_REF_RE.fullmatch(ref)
        if not match:
            continue
        path = _report_path(match.group(1))
        if path is None:
            continue
        lines = _read_lines(path)
        start, end = int(match.group(2)), int(match.group(3))
        if not 1 <= start <= end <= len(lines):
            continue
        text = "\n".join(lines[start - 1 : end]).strip()
        if text:
            output.append({"ref": ref, "source_id": "daily_report", "path": path.name,
                           "line_start": start, "line_end": end, "sha256": _sha256(path),
                           "kind": "report", "text": text})
        if len(output) >= 36:
            break
    return output


def build_evidence_bundle(
    home: Path, project_id: str, *, intelligence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    config = project_config(home, project_id)
    sources = _configured_sources(config)
    external_evidence, external_sources = evidence_for_project(
        home, project_id, limit=MAX_EXTERNAL_EVIDENCE_ITEMS,
    )
    local_evidence: list[dict[str, Any]] = []
    source_budget = MAX_EVIDENCE_ITEMS - MAX_REPORT_EVIDENCE_ITEMS - len(external_evidence)
    per_source = max(12, source_budget // max(1, len(sources)))
    for source in sources:
        local_evidence.extend(_source_evidence(source)[:per_source])
    local_evidence = local_evidence[:source_budget]
    reports = _report_evidence(home, project_id, intelligence=intelligence)[:MAX_REPORT_EVIDENCE_ITEMS]
    evidence = local_evidence + reports + external_evidence
    deduped = {item["ref"]: item for item in evidence}
    evidence = list(deduped.values())[:MAX_EVIDENCE_ITEMS]
    repo = Path(str(config.get("repo_path") or "")).expanduser().resolve()
    source_meta = [{"id": item["id"], "label": item["label"], "root": str(item["root"]),
                    "kind": "local", "exists": Path(item["root"]).is_dir()} for item in sources]
    source_meta.extend({**item, "kind": "external", "exists": True} for item in external_sources)
    state = _git_state(repo)
    fingerprint_payload = {
        "project_id": project_id, "state": state,
        "evidence": [(item["ref"], item["sha256"]) for item in evidence],
    }
    source_hash = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "project": {"id": project_id, "name": str(config.get("name") or project_id),
                    "priority": str(config.get("priority") or ""), "repo_path": str(repo)},
        "source_state": {**state, "source_hash": source_hash},
        "sources": source_meta,
        "evidence": evidence,
        "limits": {"evidence_items": len(evidence), "max_items": MAX_EVIDENCE_ITEMS,
                   "max_bundle_bytes": MAX_BUNDLE_BYTES},
    }


def _output_schema() -> dict[str, Any]:
    ref = "source:SOURCE_ID:path:Lx-Ly, report:YYYY-MM-DD:Lx-Ly, or external:SOURCE_ID:Fx"
    return {
        "project_id": "exact requested project id",
        "status": "ready|insufficient_evidence",
        "algorithm_type": "model_pipeline|hybrid_system|workflow|unknown",
        "objective": "one Chinese sentence",
        "summary": "current algorithm design in 2-5 Chinese sentences",
        "pipeline": {
            "nodes": [{"id": "stable_snake_case", "label": "short label",
                       "category": "input|preprocess|router|model|fusion|postprocess|decision|output",
                       "summary": "role in plain Chinese", "status": "current|candidate|optional|legacy|rejected|unknown",
                       "evidence": [ref]}],
            "edges": [{"source": "node id", "target": "node id", "label": "transformation",
                       "data": "semantic payload", "evidence": [ref]}],
        },
        "models": [{
            "id": "stable model id", "node_id": "matching model pipeline node", "name": "exact family",
            "variant": "deployed/current variant", "role": "why this model exists", "status": "current|candidate|optional|legacy|rejected|unknown",
            "architecture_status": "verified|partial|opaque", "architecture_summary": "plain Chinese",
            "architecture_basis": "deployment_evidence|family_reference|mixed|undisclosed",
            "input": "shape and semantics when evidenced", "output": "shape and semantics when evidenced",
            "blocks": [{"id": "stable block id", "name": "module name", "type": "module family",
                        "role": "plain Chinese explanation", "details": "channels/repeats/operation",
                        "evidence": [ref]}],
            "quantization": "INT8/FP16/etc when evidenced", "parameters": "parameter count when evidenced",
            "artifact_size": "size when evidenced", "design_rationale": ["why selected"],
            "limitations": ["known limitation"],
            "metrics": [{"name": "metric", "value": "exact value", "unit": "unit", "scope": "dataset/backend",
                         "verification": "reported|observed|platform", "evidence": [ref]}],
            "evidence": [ref],
        }],
        "design_decisions": [{"title": "decision", "status": "adopted|conditional|candidate|rejected|superseded|unknown",
                              "rationale": "why", "evidence": [ref]}],
        "alternatives": [{"name": "alternative", "status": "same decision statuses", "reason": "why kept/rejected",
                          "evidence": [ref]}],
        "algorithm_diff": [{"kind": "added|removed|changed|warning", "before": "previous belief/design",
                            "after": "current belief/design", "reason": "evidence-grounded reason", "evidence": [ref]}],
        "open_questions": [{"question": "algorithm uncertainty", "missing_evidence": "what closes it",
                            "priority": "high|medium|low", "evidence": [ref]}],
        "warnings": [{"title": "inconsistency or risk", "detail": "why it matters", "evidence": [ref]}],
    }


def _instruction(bundle: dict[str, Any], previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    instruction = {
        "task": "根据证据生成当前项目的算法方案与模型内部结构快照",
        "project": {key: bundle["project"].get(key) for key in ("id", "name", "priority")},
        "source_state": bundle["source_state"],
        "sources": [
            {key: item.get(key) for key in (
                "id", "label", "kind", "scope", "source_type", "retrieved_at", "exists",
            )}
            for item in bundle["sources"]
        ],
        "evidence_catalog": bundle["evidence"],
        "output_schema": _output_schema(),
        "rules": [
            "只分析当前 project_id，禁止混入其他项目。",
            "重点是模型、算法阶段、训练/推理策略、融合、后处理、决策规则和评测，不是文件目录或部署拓扑。",
            "所有 pipeline node、edge、model、model block、metric、decision、alternative、diff、question 和 warning 都必须引用 evidence_catalog 中的完整 ref。",
            "只能使用 evidence_catalog，不得依赖模型记忆补全网络结构。证据不足时 architecture_status=partial/opaque 或 status=insufficient_evidence。",
            "source/report 是当前项目证据；external 是经审阅的官方公开资料，只能解释项目已由 source/report 证明采用的同名模型家族。",
            "external 不能证明模型被部署、具体 checkpoint/量化参数、项目指标、项目决策或本地输入输出；这些必须引用 source/report。",
            "公开家族结构与本地采用证据结合时 architecture_basis=mixed 且 architecture_status 最多为 partial；官方未披露时使用 undisclosed/opaque。",
            "模型内部结构只保留有设计意义的层级：backbone/stage/neck/head/attention/decoder，不逐层罗列无意义算子。",
            "指标的每一个数字都必须出现在对应 evidence 文本中，且说明 scope 与 verification。",
            "明确区分当前采用、候选、可选、旧方案和已拒绝方案。",
            "发现配置、日报、评测口径或模型包之间冲突时放入 warnings，不自行裁决。",
            "用简洁中文解释专业模块的作用；模块原名保留英文。",
            "不要输出绝对路径、密钥、内部 URL 或模型下载地址。",
            "只返回一个 JSON 对象。",
        ],
    }
    if previous:
        instruction["previous_view"] = {
            "generated_at": previous.get("generated_at"),
            "objective": previous.get("objective"),
            "summary": previous.get("summary"),
            "pipeline_nodes": [
                {key: item.get(key) for key in ("id", "label", "category", "status")}
                for item in (previous.get("pipeline") or {}).get("nodes", [])
                if isinstance(item, Mapping)
            ],
            "models": [
                {key: item.get(key) for key in (
                    "id", "name", "variant", "status", "architecture_status", "architecture_basis",
                )}
                for item in previous.get("models", []) if isinstance(item, Mapping)
            ],
            "design_decisions": [
                {key: item.get(key) for key in ("title", "status")}
                for item in previous.get("design_decisions", []) if isinstance(item, Mapping)
            ],
        }
        instruction["rules"].append(
            "previous_view 只用于比较认知变化，不是事实证据；当前结论和 algorithm_diff 仍须引用本次 evidence_catalog。"
        )
    return instruction


def _request_codex(model_spec: str, instruction: dict[str, Any], repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    model_and_effort = model_spec.removeprefix("codex:")
    model, separator, reasoning = model_and_effort.partition("@")
    reasoning = reasoning if separator else "medium"
    executable = resolve_executable("RD_ALGORITHM_CODEX_BIN", "codex")
    timeout = float(os.environ.get("RD_ALGORITHM_MODEL_TIMEOUT", "1200"))
    prompt = (
        "你是算法架构审计器。严格根据标准输入中的 evidence_catalog 生成当前算法方案、模型剖面和方案演化，"
        "遵守 output_schema 与 rules，只输出 JSON。不要使用常识补全证据中不存在的网络结构或指标。"
    )
    with tempfile.TemporaryDirectory(prefix="rd-algorithm-") as temporary:
        message = Path(temporary) / "message.json"
        command = [
            executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only", "--model", model,
            "-c", 'model_provider="openai"', "-c", f'model_reasoning_effort="{reasoning}"',
            # Run away from every source repository.  The model receives the
            # bounded evidence catalog through stdin and has no project path to
            # explore, making the supplied catalog the only intended source.
            "-C", temporary,
            "--json", "--output-last-message", str(message), prompt,
        ]
        try:
            completed = subprocess.run(
                command, input=json.dumps(instruction, ensure_ascii=False), text=True,
                capture_output=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Codex request failed: {exc}") from exc
        if completed.returncode:
            raise RuntimeError(f"Codex exited with {completed.returncode}: {completed.stderr[-500:]}")
        result = _json_object(message.read_text(encoding="utf-8", errors="replace"))
        usage: dict[str, Any] = {}
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
    return result, {"model": model_spec, "provider": "codex-cli", "reasoning_effort": reasoning, "usage": usage}


def _request_claude(model: str, instruction: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    executable = os.environ.get("RD_ALGORITHM_CLAUDE_BIN", "claude")
    timeout = float(os.environ.get("RD_ALGORITHM_MODEL_TIMEOUT", "1200"))
    prompt = "你是算法架构审计器。只按输入 schema 与证据目录返回纯 JSON，不得补写无证据模型结构。"
    command = [executable, "-p", prompt, "--model", model, "--tools", "", "--disable-slash-commands",
               "--no-session-persistence", "--output-format", "json"]
    try:
        completed = subprocess.run(command, input=json.dumps(instruction, ensure_ascii=False), text=True,
                                   capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Claude route failed: {exc}") from exc
    if completed.returncode:
        raise RuntimeError(f"Claude route exited with {completed.returncode}: {completed.stderr[-500:]}")
    outer = _json_object(completed.stdout)
    value = outer.get("result", outer)
    result = value if isinstance(value, dict) else _json_object(str(value))
    return result, {"model": model, "provider": "claude-router",
                    "usage": outer.get("usage") if isinstance(outer.get("usage"), dict) else {}}


def _request_model(model: str, instruction: dict[str, Any], repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    return _request_codex(model, instruction, repo) if model.startswith("codex:") else _request_claude(model, instruction)


def _evidence_refs(value: Any, catalog: Mapping[str, dict[str, Any]], label: str) -> tuple[list[str], list[str]]:
    refs = list(dict.fromkeys(_strings(value)))
    accepted = [ref for ref in refs if ref in catalog]
    rejected = [f"{label}: unknown evidence ref {ref}" for ref in refs if ref not in catalog]
    if not accepted:
        raise ValueError(f"{label}: at least one catalog evidence ref is required")
    return accepted, rejected


def _has_project_evidence(refs: Iterable[str], catalog: Mapping[str, dict[str, Any]]) -> bool:
    return any((catalog.get(ref) or {}).get("kind") in {"source", "report"} for ref in refs)


def _require_project_evidence(
    refs: list[str], catalog: Mapping[str, dict[str, Any]], label: str,
) -> None:
    if not _has_project_evidence(refs, catalog):
        raise ValueError(f"{label}: external family references cannot prove current project state")


def _model_architecture_basis(
    refs: Iterable[str], blocks: Iterable[Mapping[str, Any]], catalog: Mapping[str, dict[str, Any]],
) -> str:
    all_refs = list(refs)
    for block in blocks:
        all_refs.extend(_strings(block.get("evidence")))
    local = _has_project_evidence(all_refs, catalog)
    external = [catalog[ref] for ref in all_refs if ref in catalog and catalog[ref].get("kind") == "external"]
    if external and all(item.get("scope") == "official_undisclosed" for item in external):
        return "undisclosed"
    if local and external:
        return "mixed"
    if external:
        return "family_reference"
    return "deployment_evidence"


def _enum(value: Any, allowed: set[str], fallback: str) -> str:
    normalized = str(value or "").strip().casefold()
    return normalized if normalized in allowed else fallback


def _validate_metrics(value: Any, catalog: Mapping[str, dict[str, Any]], label: str) -> tuple[list[dict[str, Any]], list[str]]:
    output, errors = [], []
    for index, raw in enumerate(value or []):
        if not isinstance(raw, Mapping):
            continue
        item_label = f"{label}.metrics[{index}]"
        try:
            refs, rejected = _evidence_refs(raw.get("evidence"), catalog, item_label)
            _require_project_evidence(refs, catalog, item_label)
            cited = " ".join(
                catalog[ref]["text"] for ref in refs
                if catalog[ref].get("kind") in {"source", "report"}
            )
            metric_value = _text(raw.get("value"), 200)
            if not _text(raw.get("name"), 200) or not metric_value:
                raise ValueError(f"{item_label}: metric name and value are required")
            missing = [number for number in NUMBER_RE.findall(metric_value) if number not in cited]
            if missing:
                raise ValueError(f"{item_label}: unsupported number(s): {', '.join(missing)}")
            output.append({"name": _text(raw.get("name"), 200), "value": metric_value,
                           "unit": _text(raw.get("unit"), 80), "scope": _text(raw.get("scope"), 500),
                           "verification": _enum(raw.get("verification"), {"reported", "observed", "platform"}, "reported"),
                           "evidence": refs})
            errors.extend(rejected)
        except ValueError as exc:
            errors.append(str(exc))
    return output, errors


def validate_snapshot(raw: Mapping[str, Any], bundle: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    project_id = str(bundle["project"]["id"])
    if str(raw.get("project_id") or "") != project_id:
        raise ValueError("model output changed or omitted project_id")
    catalog = {item["ref"]: item for item in bundle["evidence"]}
    errors: list[str] = []
    status = _enum(raw.get("status"), {"ready", "insufficient_evidence"}, "insufficient_evidence")

    nodes: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    pipeline = raw.get("pipeline") if isinstance(raw.get("pipeline"), Mapping) else {}
    for index, item in enumerate(pipeline.get("nodes") or []):
        if not isinstance(item, Mapping):
            continue
        label = f"pipeline.nodes[{index}]"
        try:
            refs, rejected = _evidence_refs(item.get("evidence"), catalog, label)
            _require_project_evidence(refs, catalog, label)
            node_id = _identifier(item.get("id"), f"node_{index + 1}")
            if node_id in seen_nodes:
                raise ValueError(f"{label}: duplicate id {node_id}")
            seen_nodes.add(node_id)
            nodes.append({"id": node_id, "label": _text(item.get("label"), 160) or node_id,
                          "category": _enum(item.get("category"), NODE_CATEGORIES, "model"),
                          "summary": _text(item.get("summary"), 1_200),
                          "status": _enum(item.get("status"), NODE_STATUSES, "unknown"),
                          "evidence": refs})
            errors.extend(rejected)
        except ValueError as exc:
            errors.append(str(exc))

    edges: list[dict[str, Any]] = []
    for index, item in enumerate(pipeline.get("edges") or []):
        if not isinstance(item, Mapping):
            continue
        label = f"pipeline.edges[{index}]"
        try:
            source, target = _identifier(item.get("source"), ""), _identifier(item.get("target"), "")
            if source not in seen_nodes or target not in seen_nodes or source == target:
                raise ValueError(f"{label}: edge endpoints must reference distinct accepted nodes")
            refs, rejected = _evidence_refs(item.get("evidence"), catalog, label)
            _require_project_evidence(refs, catalog, label)
            edges.append({"source": source, "target": target, "label": _text(item.get("label"), 240),
                          "data": _text(item.get("data"), 500), "evidence": refs})
            errors.extend(rejected)
        except ValueError as exc:
            errors.append(str(exc))

    models: list[dict[str, Any]] = []
    seen_models: set[str] = set()
    for index, item in enumerate(raw.get("models") or []):
        if not isinstance(item, Mapping):
            continue
        label = f"models[{index}]"
        try:
            refs, rejected = _evidence_refs(item.get("evidence"), catalog, label)
            _require_project_evidence(refs, catalog, label)
            model_id = _identifier(item.get("id"), f"model_{index + 1}")
            if model_id in seen_models:
                raise ValueError(f"{label}: duplicate id {model_id}")
            seen_models.add(model_id)
            node_id = _identifier(item.get("node_id"), "")
            if node_id not in seen_nodes:
                raise ValueError(f"{label}: node_id must reference an accepted pipeline node")
            blocks: list[dict[str, Any]] = []
            for block_index, block in enumerate(item.get("blocks") or []):
                if not isinstance(block, Mapping):
                    continue
                block_label = f"{label}.blocks[{block_index}]"
                try:
                    block_refs, block_rejected = _evidence_refs(block.get("evidence"), catalog, block_label)
                    blocks.append({"id": _identifier(block.get("id"), f"block_{block_index + 1}"),
                                   "name": _text(block.get("name"), 200), "type": _text(block.get("type"), 200),
                                   "role": _text(block.get("role"), 1_000), "details": _text(block.get("details"), 1_000),
                                   "evidence": block_refs})
                    errors.extend(block_rejected)
                except ValueError as exc:
                    errors.append(str(exc))
            metrics, metric_errors = _validate_metrics(item.get("metrics"), catalog, label)
            errors.extend(metric_errors)
            architecture_basis = _model_architecture_basis(refs, blocks, catalog)
            architecture_status = _enum(item.get("architecture_status"), MODEL_ARCH_STATUSES, "opaque")
            if architecture_basis in {"family_reference", "mixed"} and architecture_status == "verified":
                architecture_status = "partial"
                errors.append(f"{label}: public family reference cannot verify the exact deployed variant")
            if architecture_basis == "undisclosed":
                architecture_status = "opaque"
                blocks = []
            models.append({"id": model_id, "node_id": node_id, "name": _text(item.get("name"), 240),
                           "variant": _text(item.get("variant"), 240), "role": _text(item.get("role"), 1_200),
                           "status": _enum(item.get("status"), NODE_STATUSES, "unknown"),
                           "architecture_status": architecture_status,
                           "architecture_basis": architecture_basis,
                           "architecture_summary": _text(item.get("architecture_summary"), 2_000),
                           "input": _text(item.get("input"), 500), "output": _text(item.get("output"), 500),
                           "blocks": blocks, "quantization": _text(item.get("quantization"), 200),
                           "parameters": _text(item.get("parameters"), 200),
                           "artifact_size": _text(item.get("artifact_size"), 200),
                           "design_rationale": _strings(item.get("design_rationale"))[:10],
                           "limitations": _strings(item.get("limitations"))[:10],
                           "metrics": metrics, "evidence": refs})
            errors.extend(rejected)
        except ValueError as exc:
            errors.append(str(exc))

    def grounded_items(field: str, text_fields: tuple[str, ...], *, status_field: bool = False,
                       kind_field: bool = False) -> list[dict[str, Any]]:
        output = []
        for index, item in enumerate(raw.get(field) or []):
            if not isinstance(item, Mapping):
                continue
            label = f"{field}[{index}]"
            try:
                refs, rejected = _evidence_refs(item.get("evidence"), catalog, label)
                _require_project_evidence(refs, catalog, label)
                normalized = {name: _text(item.get(name), 2_000) for name in text_fields}
                if not any(normalized.values()):
                    raise ValueError(f"{label}: readable content is required")
                if status_field:
                    normalized["status"] = _enum(item.get("status"), DECISION_STATUSES, "unknown")
                if kind_field:
                    normalized["kind"] = _enum(item.get("kind"), DIFF_KINDS, "changed")
                normalized["evidence"] = refs
                output.append(normalized)
                errors.extend(rejected)
            except ValueError as exc:
                errors.append(str(exc))
        return output

    decisions = grounded_items("design_decisions", ("title", "rationale"), status_field=True)
    alternatives = grounded_items("alternatives", ("name", "reason"), status_field=True)
    diffs = grounded_items("algorithm_diff", ("before", "after", "reason"), kind_field=True)
    questions = grounded_items("open_questions", ("question", "missing_evidence", "priority"))
    for item in questions:
        item["priority"] = _enum(item.get("priority"), {"high", "medium", "low"}, "medium")
    warnings = grounded_items("warnings", ("title", "detail"))

    if status == "ready" and not nodes:
        errors.append("ready snapshot contained no accepted pipeline nodes; downgraded to insufficient_evidence")
        status = "insufficient_evidence"
    cited = set()
    for collection in (nodes, edges, models, decisions, alternatives, diffs, questions, warnings):
        for item in collection:
            cited.update(item.get("evidence") or [])
            for nested in item.get("blocks", []) if isinstance(item, Mapping) else []:
                cited.update(nested.get("evidence") or [])
            for nested in item.get("metrics", []) if isinstance(item, Mapping) else []:
                cited.update(nested.get("evidence") or [])
    return {
        "schema_version": SCHEMA_VERSION, "project_id": project_id, "project_name": bundle["project"]["name"],
        "status": status, "algorithm_type": _text(raw.get("algorithm_type"), 120) or "unknown",
        "objective": _text(raw.get("objective"), 1_000), "summary": _text(raw.get("summary"), 3_500),
        "pipeline": {"nodes": nodes, "edges": edges}, "models": models,
        "design_decisions": decisions, "alternatives": alternatives, "algorithm_diff": diffs,
        "open_questions": questions, "warnings": warnings,
        "source_state": bundle["source_state"], "sources": bundle["sources"],
        "generated_at": _utc_now(), "model_run": dict(metadata), "validation_errors": errors,
        "evidence_summary": {"bundled": len(catalog), "cited": len(cited), "models": len(models),
                             "explained_models": sum(bool(item["blocks"]) for item in models),
                             "metrics": sum(len(item["metrics"]) for item in models)},
        "evidence_catalog": {
            ref: {key: catalog[ref].get(key) for key in (
                "kind", "source_id", "path", "line_start", "line_end", "sha256", "text",
                "scope", "source_type", "url", "retrieved_at",
            )}
            for ref in sorted(cited) if ref in catalog
        },
    }


def _snapshot_root(home: Path, project_id: str) -> Path:
    return home / ".rd-cockpit" / "algorithm-architecture" / project_id


def _latest_path(home: Path, project_id: str) -> Path:
    return _snapshot_root(home, project_id) / "latest.json"


def load_snapshot(home: Path, project_id: str) -> dict[str, Any] | None:
    path = _latest_path(home, project_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def snapshot_history(home: Path, project_id: str) -> list[dict[str, Any]]:
    root = _snapshot_root(home, project_id)
    output = []
    for path in sorted(root.glob("*.json"), reverse=True) if root.is_dir() else []:
        if path.name == "latest.json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        output.append({"snapshot_id": path.stem, "generated_at": value.get("generated_at"),
                       "head": (value.get("source_state") or {}).get("head"),
                       "status": value.get("status"), "summary": value.get("summary")})
    return output


def _store_snapshot(home: Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    project_id = snapshot["project_id"]
    state = snapshot.get("source_state") or {}
    stamp = str(snapshot.get("generated_at") or _utc_now()).replace(":", "").replace("+", "_")
    head = str(state.get("head") or "nogit")[:12]
    source_hash = str(state.get("source_hash") or "unknown")[:12]
    snapshot_id = f"{stamp}-{head}-{source_hash}"
    snapshot["snapshot_id"] = snapshot_id
    root = _snapshot_root(home, project_id)
    _write_json(root / f"{snapshot_id}.json", snapshot)
    _write_json(root / "latest.json", snapshot)
    return snapshot


def _snapshot_refs(snapshot: Mapping[str, Any]) -> set[str]:
    refs: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key == "evidence":
                    refs.update(_strings(item))
                elif key != "evidence_catalog":
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(snapshot)
    return refs


def _hydrate_snapshot_evidence(home: Path, snapshot: dict[str, Any], bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Attach exact redacted excerpts to an older cached snapshot without an LLM call."""
    catalog = {item["ref"]: item for item in bundle["evidence"]}
    refs = _snapshot_refs(snapshot)
    hydrated = {
        ref: {key: catalog[ref].get(key) for key in (
            "kind", "source_id", "path", "line_start", "line_end", "sha256", "text",
            "scope", "source_type", "url", "retrieved_at",
        )}
        for ref in sorted(refs) if ref in catalog
    }
    if snapshot.get("evidence_catalog") == hydrated:
        return snapshot
    updated = dict(snapshot)
    updated["evidence_catalog"] = hydrated
    summary = dict(updated.get("evidence_summary") or {})
    summary["cited"] = len(hydrated)
    updated["evidence_summary"] = summary
    _write_json(_latest_path(home, str(updated["project_id"])), updated)
    snapshot_id = str(updated.get("snapshot_id") or "")
    if snapshot_id:
        _write_json(_snapshot_root(home, str(updated["project_id"])) / f"{snapshot_id}.json", updated)
    return updated


def _insufficient_snapshot(bundle: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "project_id": bundle["project"]["id"],
        "project_name": bundle["project"]["name"], "status": "insufficient_evidence",
        "algorithm_type": "unknown", "objective": "", "summary": reason,
        "pipeline": {"nodes": [], "edges": []}, "models": [], "design_decisions": [],
        "alternatives": [], "algorithm_diff": [], "open_questions": [], "warnings": [],
        "source_state": bundle["source_state"], "sources": bundle["sources"],
        "generated_at": _utc_now(), "model_run": {"provider": "none", "model": None, "usage": {}},
        "validation_errors": [], "evidence_summary": {"bundled": len(bundle["evidence"]), "cited": 0,
                                                        "models": 0, "explained_models": 0, "metrics": 0},
        "evidence_catalog": {},
    }


def analyze_project(
    home: Path, project_id: str, *, model: str = DEFAULT_MODEL,
    fallback_model: str = DEFAULT_FALLBACK_MODEL, force: bool = False,
    _intelligence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    bundle = build_evidence_bundle(home, project_id, intelligence=_intelligence)
    latest = load_snapshot(home, project_id)
    if (not force and latest and latest.get("schema_version") == SCHEMA_VERSION
            and (latest.get("source_state") or {}).get("source_hash") == bundle["source_state"]["source_hash"]):
        latest = _hydrate_snapshot_evidence(home, latest, bundle)
        return {**latest, "cache_hit": True}
    if len(bundle["evidence"]) < 2:
        return _store_snapshot(home, _insufficient_snapshot(bundle, "项目中没有足够的算法、模型或评测证据。"))

    repo = Path(bundle["project"]["repo_path"])
    errors = []
    for selected in dict.fromkeys([model, fallback_model]):
        if not selected:
            continue
        try:
            raw, metadata = _request_model(selected, _instruction(bundle, latest), repo)
            snapshot = validate_snapshot(raw, bundle, metadata)
            return _store_snapshot(home, snapshot)
        except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
            errors.append({"model": selected, "error": str(exc)})
    failed = _insufficient_snapshot(bundle, "模型分析失败，已保留上一个可用快照。")
    failed["status"] = "analysis_failed"
    failed["model_run"] = {"provider": "failed", "model": model, "usage": {}, "errors": errors}
    if latest:
        return {**latest, "refresh_error": errors, "cache_hit": True}
    return _store_snapshot(home, failed)


def analyze_all(
    home: Path, *, project_ids: list[str] | None = None, model: str = DEFAULT_MODEL,
    fallback_model: str = DEFAULT_FALLBACK_MODEL, force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    config = load_config(home / "config" / "projects.yaml")
    ids = project_ids or list((config.get("projects") or {}).keys())
    try:
        from .intelligence import project_intelligence

        intelligence: Mapping[str, Any] | None = project_intelligence(home, days=180, target=date.today())
    except Exception:
        intelligence = None
    results, failures = [], []
    for index, project_id in enumerate(ids, 1):
        if progress:
            progress(f"[{index}/{len(ids)}] {project_id}: 扫描证据并检查缓存…")
        try:
            snapshot = analyze_project(
                home, project_id, model=model, fallback_model=fallback_model,
                force=force, _intelligence=intelligence,
            )
            results.append({"project_id": project_id, "status": snapshot.get("status"),
                            "snapshot_id": snapshot.get("snapshot_id"), "cache_hit": bool(snapshot.get("cache_hit")),
                            "model": (snapshot.get("model_run") or {}).get("model")})
            if progress:
                suffix = "缓存命中" if snapshot.get("cache_hit") else "快照已更新"
                progress(f"[{index}/{len(ids)}] {project_id}: {snapshot.get('status')} · {suffix}")
        except Exception as exc:
            failures.append({"project_id": project_id, "error": str(exc)})
            if progress:
                progress(f"[{index}/{len(ids)}] {project_id}: 失败 · {exc}")
    return {"generated_at": _utc_now(), "projects": results, "failures": failures,
            "counts": {"total": len(ids), "ready": sum(item["status"] == "ready" for item in results),
                       "insufficient": sum(item["status"] == "insufficient_evidence" for item in results),
                       "analysis_failed": sum(item["status"] == "analysis_failed" for item in results),
                       "cached": sum(item["cache_hit"] for item in results)}}


def architecture_index(home: Path) -> dict[str, Any]:
    config = load_config(home / "config" / "projects.yaml")
    projects = []
    for project_id, item in (config.get("projects") or {}).items():
        snapshot = load_snapshot(home, project_id)
        projects.append({
            "project_id": project_id, "name": str(item.get("name") or project_id),
            "priority": str(item.get("priority") or ""),
            "status": snapshot.get("status") if snapshot else "not_analyzed",
            "summary": snapshot.get("summary") if snapshot else "尚未生成算法架构快照。",
            "algorithm_type": snapshot.get("algorithm_type") if snapshot else "unknown",
            "models": [{"id": model.get("id"), "name": model.get("name"), "variant": model.get("variant"),
                        "status": model.get("status"), "architecture_status": model.get("architecture_status"),
                        "architecture_basis": model.get("architecture_basis", "deployment_evidence")}
                       for model in (snapshot.get("models") or [])] if snapshot else [],
            "generated_at": snapshot.get("generated_at") if snapshot else None,
            "head": (snapshot.get("source_state") or {}).get("head") if snapshot else None,
            "dirty": (snapshot.get("source_state") or {}).get("dirty") if snapshot else None,
            "evidence_summary": snapshot.get("evidence_summary") if snapshot else None,
        })
    projects.sort(key=lambda value: (value["status"] != "ready", value["priority"], value["name"]))
    return {"schema_version": SCHEMA_VERSION, "generated_at": _utc_now(), "projects": projects,
            "counts": {"total": len(projects), "ready": sum(item["status"] == "ready" for item in projects),
                       "not_analyzed": sum(item["status"] == "not_analyzed" for item in projects),
                       "insufficient": sum(item["status"] == "insufficient_evidence" for item in projects),
                       "failed": sum(item["status"] == "analysis_failed" for item in projects)}}
