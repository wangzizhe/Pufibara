from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import re
import shutil
import signal
import time
from pathlib import Path
from typing import Any, Callable

from .agent_modelica_omc_workspace_v1 import (
    extract_om_success_flags,
    prepare_workspace_model_layout,
    run_check_and_simulate,
)
from .llm_provider_adapter import resolve_provider_adapter


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TASKS = REPO_ROOT / "artifacts" / "public_modelica_tasks" / "tasks.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "artifacts" / "workspace_style_probe_v0_67_0"
DOCKER_IMAGE = "openmodelica/openmodelica:v1.26.1-minimal"
RUN_PROFILE = "public_modelica_repair"
PRODUCT_REPAIR_PROFILE = "product_repair_disabled"
DEFAULT_RUN_PROFILE = "custom"
LONG_RUN_900S_PROFILE = "long_run_900s"
RUN_PROFILES: dict[str, dict[str, Any]] = {
    DEFAULT_RUN_PROFILE: {},
    LONG_RUN_900S_PROFILE: {
        "max_steps": 100,
        "max_token_budget": 999999999,
        "per_case_timeout_sec": 900,
    },
}
MAX_WORKSPACE_LIST_FILES = 200
MAX_WORKSPACE_SEARCH_FILES = 5000
MAX_WORKSPACE_SEARCH_RESULTS = 50
MAX_FILE_SLICE_LINES = 120
MAX_FILE_SLICE_LINE_CHARS = 240
MAX_TOOL_READ_CHARS = 20_000
TRUNCATED_READ_HEAD_CHARS = 12_000
TRUNCATED_READ_TAIL_CHARS = 4_000
LISTED_FILE_SUFFIXES = {".json", ".mo", ".txt", ".log"}


class ProviderStepTimeout(Exception):
    pass


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def task_to_tool_use_case(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": str(task.get("case_id") or task.get("id") or ""),
        "model_name": str(task.get("model_name") or ""),
        "model_text": str(task.get("initial_model") or task.get("model_text") or ""),
        "workflow_goal": str(
            task.get("workflow_goal")
            or task.get("description")
            or "Repair this Modelica model so that OMC check and simulation pass."
        ),
        "task_payload": task,
        "final_stop_time": float(((task.get("verification") or {}).get("simulate") or {}).get("stop_time") or 0.05),
        "final_intervals": int(((task.get("verification") or {}).get("simulate") or {}).get("intervals") or 5),
    }


def evaluate_optional_behavior(_task: dict[str, Any], _model_text: str) -> dict[str, Any]:
    return {"pass": True, "reason": "not_configured"}


def _write_case_status(case_workspace: Path, **fields: Any) -> None:
    status_path = case_workspace / "case_status.json"
    try:
        current = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    except json.JSONDecodeError:
        current = {}
    current.update(fields)
    current["updated_at_epoch"] = time.time()
    status_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_case_status(case_workspace: Path) -> dict[str, Any]:
    status_path = case_workspace / "case_status.json"
    if not status_path.exists():
        return {}
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"timeout_phase": "status_file_unreadable"}


def _provider_step_timeout_sec(config_timeout_sec: float) -> int:
    raw = str(os.getenv("GATEFORGE_WORKSPACE_PROVIDER_STEP_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            return max(1, int(float(raw)))
        except ValueError:
            pass
    return max(1, int(float(config_timeout_sec or 120.0) + 30))


def _send_tool_request_with_watchdog(adapter: Any, messages: list[dict], tools: list[dict], config: Any):
    timeout_sec = _provider_step_timeout_sec(float(getattr(config, "timeout_sec", 120.0) or 120.0))
    if not hasattr(signal, "SIGALRM"):
        return adapter.send_tool_request(messages, tools, config)

    def _raise_timeout(_signum, _frame):
        raise ProviderStepTimeout(f"provider_step_timeout:{timeout_sec}s")

    try:
        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _raise_timeout)
    except (OSError, ValueError):
        return adapter.send_tool_request(messages, tools, config)

    try:
        signal.alarm(timeout_sec)
        return adapter.send_tool_request(messages, tools, config)
    except ProviderStepTimeout as exc:
        return None, str(exc)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def _external_library_context_from_case(case: dict[str, Any]) -> dict[str, str]:
    task = case.get("task_payload") if isinstance(case.get("task_payload"), dict) else {}
    candidates = [
        case,
        task,
        task.get("engineering_metadata") if isinstance(task.get("engineering_metadata"), dict) else {},
        task.get("source_metadata") if isinstance(task.get("source_metadata"), dict) else {},
    ]
    keys = (
        "source_library_path",
        "source_package_name",
        "source_library_model_path",
        "source_qualified_model_name",
    )
    context: dict[str, str] = {}
    for key in keys:
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            value = str(candidate.get(key) or "").strip()
            if value:
                context[key] = value
                break
    return context


WORKSPACE_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "list_workspace_files",
        "description": (
            "List relevant files in the case workspace directory. Large mirrored external "
            "library trees and generated compiler artifacts may be summarized instead of "
            "listed exhaustively."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read the contents of any file in the workspace. Use this to inspect model "
            "definitions, connector types, and available libraries. Large files may be "
            "returned as bounded head/tail excerpts with metadata. If a large file is "
            "truncated, use search_workspace_files and read_file_slice to inspect the "
            "needed middle sections."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_workspace_files",
        "description": (
            "Search text files in the workspace for a literal pattern. This is a neutral "
            "file navigation tool for locating model definitions, variables, parameters, "
            "or compiler text in large workspaces; it does not diagnose or repair models."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Literal text to search for."},
                "glob": {
                    "type": "string",
                    "description": "Workspace-relative glob to search; defaults to **/*.mo.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matches to return, capped by the harness.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file_slice",
        "description": (
            "Read a bounded line range from a workspace file. Use this after read_file "
            "truncation or search_workspace_files results to inspect a specific section "
            "without loading the whole file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root."},
                "start_line": {"type": "integer", "description": "1-based first line to read."},
                "line_count": {"type": "integer", "description": "Number of lines to read."},
            },
            "required": ["path", "start_line", "line_count"],
        },
    },
    {
        "name": "write_and_check_candidate_model",
        "description": (
            "Write a complete candidate Modelica model into the transparent case workspace "
            "and immediately run OMC checkModel AND simulate with the task's final-evaluation "
            "simulation settings. Returns raw compiler output, basic pass flags, and the "
            "raw compiler-output artifact path. "
            "This does not select candidates. If you explicitly choose this candidate "
            "as final, set submit_if_passes=true; the harness submits it only if the "
            "same accepted simulation policy used by final evaluation passes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string", "description": "Short candidate identifier."},
                "model_text": {"type": "string", "description": "Complete Modelica model source."},
                "rationale": {"type": "string", "description": "Brief reason for this candidate."},
                "submit_if_passes": {
                    "type": "boolean",
                    "description": (
                        "Set true only when you explicitly choose this candidate as final. "
                        "The harness submits it only if the accepted final-evaluation "
                        "policy passes, including warning-pass simulation outcomes."
                    ),
                },
            },
            "required": ["candidate_id", "model_text"],
        },
    },
    {
        "name": "submit_candidate_model",
        "description": (
            "Submit a previously written candidate model for final evaluation. "
            "The harness will evaluate the submitted file with checkModel and simulate; "
            "it will not choose candidates or submit automatically."
        ),
        "parameters": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}},
            "required": ["candidate_id"],
        },
    },
    {
        "name": "update_repair_progress",
        "description": (
            "Track your repair progress with a structured task list. "
            "Use this to track your repair plan, mark completed analysis steps, "
            "and track which fixes have been attempted. Helps prevent thrashing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "Task description."},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "cancelled"]},
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
    {
        "name": "batch_check_candidates",
        "description": (
            "Write and check multiple candidate models in one call. "
            "Use this to test several complete candidate models simultaneously. "
            "Returns basic pass flags and raw compiler-output artifact paths for each."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "candidate_id": {"type": "string"},
                            "model_text": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["candidate_id", "model_text"],
                    },
                },
            },
            "required": ["candidates"],
        },
    },
]


def _safe_candidate_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text[:80] or "candidate"


def _model_text_artifact_ref(model_text: str, *, candidate_id: str = "") -> dict[str, Any]:
    text = str(model_text or "")
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "char_count": len(text),
        "candidate_id": _safe_candidate_id(candidate_id) if candidate_id else "",
    }


def _redact_model_text_fields(value: Any, *, candidate_id: str = "") -> Any:
    if isinstance(value, dict):
        local_candidate_id = str(value.get("candidate_id") or candidate_id or "")
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key == "model_text":
                if isinstance(item, dict) and "sha256" in item and "char_count" in item:
                    redacted[key] = dict(item)
                else:
                    redacted[key] = _model_text_artifact_ref(
                        str(item or ""),
                        candidate_id=local_candidate_id,
                    )
            else:
                redacted[key] = _redact_model_text_fields(item, candidate_id=local_candidate_id)
        return redacted
    if isinstance(value, list):
        return [_redact_model_text_fields(item, candidate_id=candidate_id) for item in value]
    return value


def _redact_tool_arguments_for_record(arguments: dict[str, Any]) -> dict[str, Any]:
    return _redact_model_text_fields(dict(arguments or {}))


def _redact_result_for_artifact(result: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(result or {})
    steps: list[Any] = []
    for step in redacted.get("steps") or []:
        if not isinstance(step, dict):
            steps.append(step)
            continue
        step_copy = dict(step)
        if isinstance(step_copy.get("tool_calls"), list):
            calls: list[Any] = []
            for call in step_copy.get("tool_calls") or []:
                if not isinstance(call, dict):
                    calls.append(call)
                    continue
                call_copy = dict(call)
                if isinstance(call_copy.get("arguments"), dict):
                    call_copy["arguments"] = _redact_tool_arguments_for_record(call_copy["arguments"])
                calls.append(call_copy)
            step_copy["tool_calls"] = calls
        steps.append(step_copy)
    redacted["steps"] = steps
    return redacted


def _extract_model_name(text: str) -> str:
    match = re.search(r"^\s*model\s+(\w+)", text, re.MULTILINE)
    return match.group(1) if match else "model"


def _is_external_library_mirror(path: Path) -> bool:
    return path.is_dir() and (path / "package.mo").exists()


def _is_listable_workspace_file(path: Path) -> bool:
    if path.name == "case_status.json":
        return True
    if path.suffix in LISTED_FILE_SUFFIXES:
        return True
    if path.name.endswith(".omc.txt"):
        return True
    return False


def _workspace_file_listing(workspace: Path) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    omitted_generated_count = 0
    truncated = False
    if not workspace.exists():
        return {"files": items, "omitted": omitted, "truncated": truncated}

    for entry in sorted(workspace.iterdir()):
        if entry.is_dir() and _is_external_library_mirror(entry):
            omitted.append(
                {
                    "path": f"{entry.name}/",
                    "type": "directory",
                    "reason": "external_library_mirror",
                }
            )
            continue
        if entry.is_dir():
            for path in sorted(entry.rglob("*")):
                if not path.is_file():
                    continue
                if not _is_listable_workspace_file(path):
                    omitted_generated_count += 1
                    continue
                rel = str(path.relative_to(workspace))
                items.append({"path": rel, "size": path.stat().st_size, "type": path.suffix})
                if len(items) >= MAX_WORKSPACE_LIST_FILES:
                    truncated = True
                    break
            if truncated:
                break
            continue
        if not entry.is_file():
            continue
        if not _is_listable_workspace_file(entry):
            omitted_generated_count += 1
            continue
        rel = str(entry.relative_to(workspace))
        items.append({"path": rel, "size": entry.stat().st_size, "type": entry.suffix})
        if len(items) >= MAX_WORKSPACE_LIST_FILES:
            truncated = True
            break

    if omitted_generated_count:
        omitted.append(
            {
                "count": omitted_generated_count,
                "reason": "generated_or_binary_artifact",
            }
        )
    return {
        "files": items,
        "omitted": omitted,
        "truncated": truncated,
        "max_files": MAX_WORKSPACE_LIST_FILES,
    }


def _bounded_read_file(path: Path, *, display_path: str) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        content = path.read_text(encoding="latin-1")
    if len(content) <= MAX_TOOL_READ_CHARS:
        return content
    return json.dumps(
        {
            "path": display_path,
            "size_chars": len(content),
            "truncated": True,
            "max_chars": MAX_TOOL_READ_CHARS,
            "head": content[:TRUNCATED_READ_HEAD_CHARS],
            "tail": content[-TRUNCATED_READ_TAIL_CHARS:],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _resolve_workspace_file(workspace: Path, file_path: str) -> tuple[Path | None, dict[str, Any] | None]:
    normalized = str(file_path or "").strip()
    if not normalized:
        return None, {"error": "path required"}
    full_path = (workspace / normalized).resolve()
    if not full_path.is_relative_to(workspace.resolve()):
        return None, {"error": "path traversal denied", "path": normalized}
    if not full_path.exists():
        return None, {"error": "file not found", "path": normalized}
    if not full_path.is_file():
        return None, {"error": "path is not a file", "path": normalized}
    return full_path, None


def _is_searchable_workspace_file(path: Path) -> bool:
    if path.name.endswith(".omc.txt"):
        return True
    return path.suffix in LISTED_FILE_SUFFIXES


def _search_workspace_files(
    workspace: Path,
    *,
    pattern: str,
    glob_pattern: str = "**/*.mo",
    max_results: int = MAX_WORKSPACE_SEARCH_RESULTS,
) -> dict[str, Any]:
    literal = str(pattern or "")
    if not literal:
        return {"error": "pattern required"}
    glob_text = str(glob_pattern or "").strip() or "**/*.mo"
    if Path(glob_text).is_absolute():
        return {"error": "glob must be workspace-relative", "glob": glob_text}
    max_matches = min(MAX_WORKSPACE_SEARCH_RESULTS, max(1, int(max_results or MAX_WORKSPACE_SEARCH_RESULTS)))
    matches: list[dict[str, Any]] = []
    scanned_file_count = 0
    file_scan_truncated = False
    try:
        candidates = sorted(workspace.glob(glob_text))
    except (NotImplementedError, ValueError) as exc:
        return {"error": f"invalid glob: {exc}", "glob": glob_text}
    for path in candidates:
        if not path.is_file() or not _is_searchable_workspace_file(path):
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(workspace.resolve()):
            continue
        scanned_file_count += 1
        if scanned_file_count > MAX_WORKSPACE_SEARCH_FILES:
            file_scan_truncated = True
            break
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
            with handle:
                for line_number, line in enumerate(handle, 1):
                    column = line.find(literal)
                    if column < 0:
                        continue
                    matches.append(
                        {
                            "path": str(path.relative_to(workspace)),
                            "line": line_number,
                            "column": column + 1,
                            "preview": line.strip()[:240],
                        }
                    )
                    if len(matches) >= max_matches:
                        return {
                            "pattern": literal,
                            "glob": glob_text,
                            "matches": matches,
                            "match_count": len(matches),
                            "scanned_file_count": scanned_file_count,
                            "truncated": True,
                            "file_scan_truncated": file_scan_truncated,
                            "max_results": max_matches,
                            "max_scanned_files": MAX_WORKSPACE_SEARCH_FILES,
                        }
        except OSError:
            continue
    return {
        "pattern": literal,
        "glob": glob_text,
        "matches": matches,
        "match_count": len(matches),
        "scanned_file_count": scanned_file_count,
        "truncated": file_scan_truncated,
        "file_scan_truncated": file_scan_truncated,
        "max_results": max_matches,
        "max_scanned_files": MAX_WORKSPACE_SEARCH_FILES,
    }


def _read_file_slice(
    path: Path,
    *,
    display_path: str,
    start_line: int,
    line_count: int,
) -> dict[str, Any]:
    start = max(1, int(start_line or 1))
    requested_count = max(1, int(line_count or 1))
    bounded_count = min(MAX_FILE_SLICE_LINES, requested_count)
    end = start + bounded_count - 1
    content_lines: list[str] = []
    line_count_read = 0
    truncated_line_count = 0
    has_more_after = False
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
        with handle:
            for line_number, line in enumerate(handle, 1):
                if line_number < start:
                    continue
                if line_number > end:
                    has_more_after = True
                    break
                text = line.rstrip("\n")
                if len(text) > MAX_FILE_SLICE_LINE_CHARS:
                    text = text[:MAX_FILE_SLICE_LINE_CHARS] + "...[line_truncated]"
                    truncated_line_count += 1
                content_lines.append(f"{line_number}: {text}")
                line_count_read += 1
    except OSError as exc:
        return {"error": f"read failed: {exc}", "path": display_path}
    return {
        "path": display_path,
        "start_line": start,
        "end_line": start + line_count_read - 1 if line_count_read else start - 1,
        "requested_line_count": requested_count,
        "line_count": line_count_read,
        "max_line_count": MAX_FILE_SLICE_LINES,
        "max_line_chars": MAX_FILE_SLICE_LINE_CHARS,
        "truncated": requested_count > bounded_count or truncated_line_count > 0,
        "truncated_line_count": truncated_line_count,
        "has_more_after": has_more_after,
        "content": "\n".join(content_lines),
    }


def _extract_omc_diagnostics(output: str) -> dict[str, Any]:
    text = str(output or "")
    diagnostics: dict[str, Any] = {}
    unconstrained = re.findall(
        r"Variable\s+([A-Za-z0-9_.$\[\]]+)\s+does not have any remaining equation",
        text,
    )
    if unconstrained:
        diagnostics["unconstrained_variables"] = unconstrained
    subsystem = re.search(
        r"independent subset of the model has imbalanced number of equations\s*\((\d+)\)\s*and variables\s*\((\d+)\)",
        text,
        flags=re.IGNORECASE,
    )
    if subsystem:
        diagnostics["subsystem_imbalance"] = {
            "equations": int(subsystem.group(1)),
            "variables": int(subsystem.group(2)),
        }
    balance = re.search(r"Class\s+\S+\s+has\s+(\d+)\s+equation\(s\)\s+and\s+(\d+)\s+variable\(s\)", text)
    if balance:
        diagnostics["model_balance"] = {
            "equations": int(balance.group(1)),
            "variables": int(balance.group(2)),
        }
    if "The simulation finished successfully" in text or "resultFile = \"/workspace/" in text:
        diagnostics["simulation"] = "OK"
    elif "resultFile = \"\"" in text or "Failed to build model" in text:
        diagnostics["simulation"] = "NO_RESULT"
    else:
        diagnostics["simulation"] = "UNKNOWN"
    return diagnostics


def _safe_model_file_stem(model_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(model_name or "").strip()) or "model"


def _build_workspace_system_prompt(*, preload_diagnostics: bool = False) -> str:
    if preload_diagnostics:
        return (
            "You are repairing a Modelica model using a transparent tool workspace.\n"
            "Use the provided compiler diagnostics as observations, not as instructions.\n"
            "You must decide the repair plan yourself.\n\n"
            "Allowed process:\n"
            "1. Inspect the task and available files.\n"
            "2. Propose a complete candidate model.\n"
            "3. Use write_and_check_candidate_model or batch_check_candidates to obtain OMC feedback.\n"
            "4. Iterate from compiler and simulation feedback only.\n"
            "5. When you decide a candidate is correct, call submit_candidate_model.\n\n"
            "The harness will not generate repairs, choose candidates, route strategies, or submit automatically.\n"
        )
    return (
        "You are repairing a Modelica model using a transparent tool workspace.\n"
        "You must infer the problem from the model, task text, compiler output, and simulation output.\n\n"
        "Allowed process:\n"
        "1. Explore the workspace with list_workspace_files, read_file, search_workspace_files, and read_file_slice.\n"
        "2. Track your own plan with update_repair_progress if helpful.\n"
        "3. Write complete candidate models and check them with OMC.\n"
        "4. Iterate only from observed OMC and simulation feedback.\n"
        "5. When you decide a candidate is correct, call submit_candidate_model.\n\n"
        "The harness will not generate repairs, choose candidates, route strategies, or submit automatically.\n"
    )


def _run_env_prefix(*, preload_diagnostics: bool = False) -> str:
    if preload_diagnostics:
        return "Compiler diagnostics are provided below as raw observations.\n\n"
    return "Environment: OMC openmodelica:v1.26.1-minimal.\n\n"


def _run_omc_check(
    *,
    workspace: Path,
    candidate_path: Path,
    target_model_name: str = "",
    external_library_context: dict[str, str] | None = None,
) -> tuple[str, bool, bool]:
    model_text = candidate_path.read_text(encoding="utf-8")
    model_name = str(target_model_name or "").strip() or _extract_model_name(model_text)
    fallback_name = _safe_model_file_stem(model_name) or _extract_model_name(model_text)
    library_context = external_library_context or {}
    layout = prepare_workspace_model_layout(
        workspace=workspace,
        fallback_model_path=Path(f"{fallback_name}.mo"),
        primary_model_name=model_name,
        source_library_path=str(library_context.get("source_library_path") or ""),
        source_package_name=str(library_context.get("source_package_name") or ""),
        source_library_model_path=str(library_context.get("source_library_model_path") or ""),
        source_qualified_model_name=str(library_context.get("source_qualified_model_name") or model_name),
    )
    layout.model_write_path.write_text(model_text, encoding="utf-8")
    _, output, check_ok, simulate_ok = run_check_and_simulate(
        workspace=workspace,
        model_load_files=list(layout.model_load_files),
        model_name=layout.model_identifier,
        timeout_sec=180,
        backend="openmodelica_docker",
        docker_image=DOCKER_IMAGE,
        stop_time=0.05,
        intervals=100,
        extra_model_loads=[],
    )
    return str(output or ""), bool(check_ok), bool(simulate_ok)


def _run_omc_simulate(
    *,
    workspace: Path,
    candidate_path: Path,
    stop_time: float,
    intervals: int,
    method: str = "",
    target_model_name: str = "",
    external_library_context: dict[str, str] | None = None,
) -> tuple[str, bool, bool]:
    model_text = candidate_path.read_text(encoding="utf-8")
    model_name = str(target_model_name or "").strip() or _extract_model_name(model_text)
    fallback_name = _safe_model_file_stem(model_name) or _extract_model_name(model_text)
    library_context = external_library_context or {}
    layout = prepare_workspace_model_layout(
        workspace=workspace,
        fallback_model_path=Path(f"{fallback_name}.mo"),
        primary_model_name=model_name,
        source_library_path=str(library_context.get("source_library_path") or ""),
        source_package_name=str(library_context.get("source_package_name") or ""),
        source_library_model_path=str(library_context.get("source_library_model_path") or ""),
        source_qualified_model_name=str(library_context.get("source_qualified_model_name") or model_name),
    )
    layout.model_write_path.write_text(model_text, encoding="utf-8")
    _, output, check_ok, simulate_ok = run_check_and_simulate(
        workspace=workspace,
        model_load_files=list(layout.model_load_files),
        model_name=layout.model_identifier,
        timeout_sec=180,
        backend="openmodelica_docker",
        docker_image=DOCKER_IMAGE,
        stop_time=float(stop_time),
        intervals=int(intervals),
        method=str(method or ""),
        extra_model_loads=[],
    )
    return str(output or ""), bool(check_ok), bool(simulate_ok)


def _omc_policy_metadata(
    output: str,
    *,
    check_ok: bool,
    simulate_ok: bool,
) -> dict[str, Any]:
    """Normalize OMC output into the acceptance policy used by final eval."""
    text = str(output or "")
    lower = text.lower()
    result_match = re.search(r'resultfile\s*=\s*"([^"]*)"', text, flags=re.IGNORECASE)
    has_result_file = bool(result_match and result_match.group(1).strip())
    success_message = "the simulation finished successfully" in lower
    fatal_markers = (
        "simulation execution failed",
        "error occurred while solving",
        "division by zero",
        "integrator failed",
    )
    fatal_failure = any(marker in lower for marker in fatal_markers)
    warning_markers = [
        marker
        for marker, token in (
            ("assertion", "assertion"),
            ("invalid_root", "invalid root"),
            ("min_max_warning", "min/max"),
        )
        if token in lower
    ]
    accepted_simulate_ok = bool(
        simulate_ok
        or (check_ok and has_result_file and success_message and not fatal_failure)
    )
    if simulate_ok:
        simulation_status = "clean_pass"
    elif accepted_simulate_ok:
        simulation_status = "warning_pass"
    else:
        simulation_status = "fail"
    return {
        "accepted_check_ok": bool(check_ok),
        "accepted_simulate_ok": bool(accepted_simulate_ok),
        "policy_pass": bool(check_ok and accepted_simulate_ok),
        "strict_simulate_ok": bool(simulate_ok),
        "simulation_status": simulation_status,
        "simulation_warning_markers": warning_markers,
    }


def _dispatch_workspace_tool(
    *,
    name: str,
    arguments: dict[str, Any],
    workspace: Path,
    candidate_paths: dict[str, Path],
    candidate_meta: dict[str, dict[str, Any]],
    target_model_name: str = "",
    deficit_state: dict[str, int] | None = None,
    external_library_context: dict[str, str] | None = None,
) -> str:
    candidate_id = _safe_candidate_id(str(arguments.get("candidate_id") or "candidate"))

    if name == "list_workspace_files":
        return json.dumps(_workspace_file_listing(workspace), sort_keys=True)

    if name == "read_file":
        file_path = str(arguments.get("path") or "").strip()
        full_path, error = _resolve_workspace_file(workspace, file_path)
        if error:
            return json.dumps(error, sort_keys=True)
        assert full_path is not None
        return _bounded_read_file(full_path, display_path=file_path)

    if name == "search_workspace_files":
        try:
            max_results = int(arguments.get("max_results") or MAX_WORKSPACE_SEARCH_RESULTS)
        except (TypeError, ValueError):
            return json.dumps({"error": "max_results must be an integer"}, sort_keys=True)
        return json.dumps(
            _search_workspace_files(
                workspace,
                pattern=str(arguments.get("pattern") or ""),
                glob_pattern=str(arguments.get("glob") or "**/*.mo"),
                max_results=max_results,
            ),
            sort_keys=True,
        )

    if name == "read_file_slice":
        file_path = str(arguments.get("path") or "").strip()
        full_path, error = _resolve_workspace_file(workspace, file_path)
        if error:
            return json.dumps(error, sort_keys=True)
        try:
            start_line = int(arguments.get("start_line") or 1)
            line_count = int(arguments.get("line_count") or MAX_FILE_SLICE_LINES)
        except (TypeError, ValueError):
            return json.dumps({"error": "start_line and line_count must be integers"}, sort_keys=True)
        assert full_path is not None
        return json.dumps(
            _read_file_slice(
                full_path,
                display_path=file_path,
                start_line=start_line,
                line_count=line_count,
            ),
            sort_keys=True,
        )

    if name == "write_and_check_candidate_model":
        model_text = str(arguments.get("model_text") or "")
        submit_if_passes_requested = bool(arguments.get("submit_if_passes"))
        if not model_text.strip():
            return json.dumps({"error": "model_text required"}, sort_keys=True)
        path = workspace / f"{candidate_id}.mo"
        path.write_text(model_text, encoding="utf-8")
        candidate_paths[candidate_id] = path
        output, check_ok, simulate_ok = _run_omc_check(
            workspace=workspace,
            candidate_path=path,
            target_model_name=target_model_name,
            external_library_context=external_library_context,
        )
        omc_output_path = workspace / f"{candidate_id}.omc.txt"
        omc_output_path.write_text(str(output or ""), encoding="utf-8")
        policy_meta = _omc_policy_metadata(
            output,
            check_ok=bool(check_ok),
            simulate_ok=bool(simulate_ok),
        )
        auto_submitted_after_llm_request = bool(
            submit_if_passes_requested and policy_meta["policy_pass"]
        )
        candidate_meta[candidate_id] = {
            "candidate_id": candidate_id,
            "path": str(path),
            "rationale": str(arguments.get("rationale") or ""),
            "model_name": str(target_model_name or "").strip() or _extract_model_name(model_text),
            "write_check_ok": bool(policy_meta["accepted_check_ok"]),
            "write_simulate_ok": bool(policy_meta["accepted_simulate_ok"]),
            "write_raw_check_ok": bool(check_ok),
            "write_raw_simulate_ok": bool(simulate_ok),
            "write_policy_pass": bool(policy_meta["policy_pass"]),
            "write_strict_simulate_ok": bool(policy_meta["strict_simulate_ok"]),
            "write_simulation_status": str(policy_meta["simulation_status"]),
            "write_simulation_warning_markers": list(policy_meta["simulation_warning_markers"]),
            "omc_output_path": str(omc_output_path),
            "submit_if_passes_requested": submit_if_passes_requested,
            "auto_submitted_after_llm_request": auto_submitted_after_llm_request,
        }
        return json.dumps(
            {
                "status": (
                    "submitted_after_pass"
                    if auto_submitted_after_llm_request
                    else "written_and_checked"
                ),
                "candidate_id": candidate_id,
                "path": str(path),
                "check_ok": bool(policy_meta["accepted_check_ok"]),
                "simulate_ok": bool(policy_meta["accepted_simulate_ok"]),
                "raw_check_ok": bool(check_ok),
                "raw_simulate_ok": bool(simulate_ok),
                "accepted_simulate_ok": bool(policy_meta["accepted_simulate_ok"]),
                "policy_pass": bool(policy_meta["policy_pass"]),
                "strict_simulate_ok": bool(policy_meta["strict_simulate_ok"]),
                "simulation_status": str(policy_meta["simulation_status"]),
                "simulation_warning_markers": list(policy_meta["simulation_warning_markers"]),
                "omc_output": str(output or "")[:3000],
                "omc_output_path": str(omc_output_path),
                "auto_repair": False,
                "auto_submit": False,
                "candidate_selected": False,
                "submit_if_passes_requested": submit_if_passes_requested,
                "auto_submitted_after_llm_request": auto_submitted_after_llm_request,
            },
            sort_keys=True,
        )

    if name == "update_repair_progress":
        todos = arguments.get("todos") if isinstance(arguments.get("todos"), list) else []
        return json.dumps({
            "status": "recorded",
            "todo_count": len(todos),
            "completed": sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "completed"),
            "in_progress": sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "in_progress"),
            "pending": sum(1 for t in todos if isinstance(t, dict) and t.get("status") == "pending"),
        }, sort_keys=True)

    if name == "batch_check_candidates":
        candidates = arguments.get("candidates") if isinstance(arguments.get("candidates"), list) else []
        results: list[dict[str, Any]] = []
        for cand in candidates[:6]:
            if not isinstance(cand, dict):
                continue
            cid = _safe_candidate_id(str(cand.get("candidate_id") or "batch_candidate"))
            model_text = str(cand.get("model_text") or "")
            if not model_text.strip():
                continue
            path = workspace / f"{cid}.mo"
            path.write_text(model_text, encoding="utf-8")
            candidate_paths[cid] = path
            output, check_ok, simulate_ok = _run_omc_check(
                workspace=workspace,
                candidate_path=path,
                target_model_name=target_model_name,
                external_library_context=external_library_context,
            )
            omc_output_path = workspace / f"{cid}.omc.txt"
            omc_output_path.write_text(str(output or ""), encoding="utf-8")
            policy_meta = _omc_policy_metadata(
                output,
                check_ok=bool(check_ok),
                simulate_ok=bool(simulate_ok),
            )
            candidate_meta[cid] = {
                "candidate_id": cid,
                "path": str(path),
                "rationale": str(cand.get("rationale", "")),
                "model_name": str(target_model_name or "").strip() or _extract_model_name(model_text),
                "write_check_ok": bool(policy_meta["accepted_check_ok"]),
                "write_simulate_ok": bool(policy_meta["accepted_simulate_ok"]),
                "write_raw_check_ok": bool(check_ok),
                "write_raw_simulate_ok": bool(simulate_ok),
                "write_policy_pass": bool(policy_meta["policy_pass"]),
                "write_strict_simulate_ok": bool(policy_meta["strict_simulate_ok"]),
                "write_simulation_status": str(policy_meta["simulation_status"]),
                "write_simulation_warning_markers": list(policy_meta["simulation_warning_markers"]),
                "omc_output_path": str(omc_output_path),
            }
            results.append({
                "candidate_id": cid,
                "rationale": str(cand.get("rationale", ""))[:80],
                "check_ok": bool(policy_meta["accepted_check_ok"]),
                "simulate_ok": bool(policy_meta["accepted_simulate_ok"]),
                "raw_check_ok": bool(check_ok),
                "raw_simulate_ok": bool(simulate_ok),
                "policy_pass": bool(policy_meta["policy_pass"]),
                "strict_simulate_ok": bool(policy_meta["strict_simulate_ok"]),
                "simulation_status": str(policy_meta["simulation_status"]),
                "simulation_warning_markers": list(policy_meta["simulation_warning_markers"]),
                "omc_output_path": str(omc_output_path),
            })
        return json.dumps({"batch_results": results, "total": len(results)}, sort_keys=True)

    if candidate_id not in candidate_paths:
        return json.dumps({"error": "unknown_candidate_id", "candidate_id": candidate_id}, sort_keys=True)

    path = candidate_paths[candidate_id]

    if name == "submit_candidate_model":
        return json.dumps({"status": "submitted", "candidate_id": candidate_id}, sort_keys=True)

    return json.dumps({"error": "unknown_tool", "tool": name}, sort_keys=True)


def run_workspace_style_case(
    case: dict[str, Any],
    *,
    out_dir: Path,
    max_steps: int = 10,
    max_token_budget: int = 32000,
    planner_backend: str = "auto",
    preload_diagnostics: str | None = None,
) -> dict[str, Any]:
    case_id = str(case["case_id"])
    model_name = str(case["model_name"])
    current_text = str(case["model_text"])
    workflow_goal = str(case.get("workflow_goal") or "")
    external_library_context = _external_library_context_from_case(case)
    case_workspace = (out_dir / "workspaces" / case_id).resolve()
    if case_workspace.exists():
        shutil.rmtree(case_workspace, ignore_errors=True)
    case_workspace.mkdir(parents=True, exist_ok=True)
    (case_workspace / "initial.mo").write_text(current_text, encoding="utf-8")
    case_started_at = time.monotonic()
    _write_case_status(case_workspace, timeout_phase="case_started", step=0)

    _write_case_status(case_workspace, timeout_phase="resolve_provider_adapter", step=0)
    adapter, config = resolve_provider_adapter(planner_backend)
    provider = config.provider_name
    _write_case_status(case_workspace, timeout_phase="provider_resolved", provider=provider, step=0)
    if provider == "rule":
        return {
            "case_id": case_id,
            "model_name": model_name,
            "provider": provider,
            "final_verdict": "FAILED",
            "provider_error": "rule_backend_selected",
            "submitted": False,
            "steps": [],
            "candidate_files": [],
        }

    system_prompt = _build_workspace_system_prompt(preload_diagnostics=bool(preload_diagnostics))
    env_prefix = _run_env_prefix(preload_diagnostics=bool(preload_diagnostics))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"{env_prefix}"
                + (f"{preload_diagnostics}\n\n" if preload_diagnostics else "")
                + f"Task: {workflow_goal}\n\n"
                f"Model name: {model_name}\n"
                f"Workspace initial file: initial.mo\n\n"
                f"Initial model:\n-----BEGIN_MODEL-----\n{current_text}\n-----END_MODEL-----\n"
            ),
        },
    ]
    candidate_paths: dict[str, Path] = {}
    candidate_meta: dict[str, dict[str, Any]] = {}
    steps: list[dict[str, Any]] = []
    token_used = 0
    submitted_id = ""
    submission_mode = "none"
    provider_error = ""
    stop_reason = "max_steps_exhausted"
    budget_exceeded_at_step = 0
    invalid_submission_attempts: list[dict[str, Any]] = []
    for step_idx in range(1, max(1, int(max_steps)) + 1):
        _write_case_status(
            case_workspace,
            timeout_phase="provider_request",
            provider=provider,
            step=step_idx,
            token_used=token_used,
        )
        resp, err = _send_tool_request_with_watchdog(adapter, messages, WORKSPACE_TOOL_DEFS, config)
        _write_case_status(
            case_workspace,
            timeout_phase="provider_response_received",
            provider=provider,
            provider_error=err or "",
            step=step_idx,
            token_used=token_used,
        )
        if err:
            provider_error = err
            steps.append({"step": step_idx, "error": err})
            stop_reason = "provider_error"
            break
        if resp is None:
            steps.append({"step": step_idx, "error": "null_response"})
            stop_reason = "null_response"
            break
        token_used += int(resp.usage.get("total_tokens", 0))
        reasoning_text = resp.reasoning or ""

        step_record = {
            "step": step_idx,
            "text": resp.text,
            "reasoning": reasoning_text[:3000] if reasoning_text else "",
            "tool_calls": [
                {"name": tc.name, "arguments": _redact_tool_arguments_for_record(tc.arguments)}
                for tc in resp.tool_calls
            ],
            "token_used": token_used,
        }
        if resp.tool_calls:
            assistant_msg = {"role": "assistant", "content": resp.text or None}
            if reasoning_text:
                assistant_msg["reasoning_content"] = reasoning_text
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in resp.tool_calls
            ]
            messages.append(assistant_msg)
            tool_results = []
            for tc in resp.tool_calls:
                _write_case_status(
                    case_workspace,
                    timeout_phase="tool_call",
                    provider=provider,
                    step=step_idx,
                    tool_name=tc.name,
                    token_used=token_used,
                )
                result = _dispatch_workspace_tool(
                    name=tc.name,
                    arguments=dict(tc.arguments),
                    workspace=case_workspace,
                    candidate_paths=candidate_paths,
                    candidate_meta=candidate_meta,
                    target_model_name=model_name,
                    external_library_context=external_library_context,
                )
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                tool_results.append({
                    "name": tc.name,
                    "result": result,
                    "result_preview": result[:500],
                })
                if tc.name == "submit_candidate_model":
                    requested_candidate_id = _safe_candidate_id(
                        str(tc.arguments.get("candidate_id") or "")
                    )
                    try:
                        submit_payload = json.loads(result)
                    except json.JSONDecodeError:
                        submit_payload = {}
                    if (
                        submit_payload.get("status") == "submitted"
                        and requested_candidate_id in candidate_paths
                    ):
                        submitted_id = requested_candidate_id
                        submission_mode = "llm"
                    else:
                        invalid_submission_attempts.append({
                            "candidate_id": requested_candidate_id,
                            "result": result,
                        })
                if tc.name == "write_and_check_candidate_model":
                    try:
                        write_payload = json.loads(result)
                    except json.JSONDecodeError:
                        write_payload = {}
                    requested_candidate_id = _safe_candidate_id(
                        str(tc.arguments.get("candidate_id") or "")
                    )
                    if (
                        write_payload.get("status") == "submitted_after_pass"
                        and requested_candidate_id in candidate_paths
                    ):
                        submitted_id = requested_candidate_id
                        submission_mode = "llm_submit_if_passes"
            step_record["tool_results"] = tool_results
            _write_case_status(
                case_workspace,
                timeout_phase="tool_results_appended",
                provider=provider,
                step=step_idx,
                token_used=token_used,
            )
        else:
            messages.append({"role": "assistant", "content": resp.text})
        steps.append(step_record)
        _write_case_status(
            case_workspace,
            timeout_phase="step_completed",
            provider=provider,
            step=step_idx,
            token_used=token_used,
        )
        if submitted_id:
            stop_reason = "submitted"
            break
        if token_used >= max_token_budget:
            stop_reason = "token_budget_exceeded"
            budget_exceeded_at_step = step_idx
            break

    final_model_text = ""
    final_verdict = "FAILED"
    final_simulation_status = ""
    final_strict_simulate_ok = False
    final_simulation_warning_markers: list[str] = []

    if submitted_id and submitted_id in candidate_paths:
        _write_case_status(
            case_workspace,
            timeout_phase="final_eval",
            provider=provider,
            submitted_candidate_id=submitted_id,
            token_used=token_used,
        )
        final_model_text = candidate_paths[submitted_id].read_text(encoding="utf-8")
        final_output, check_ok, simulate_ok = _run_omc_simulate(
            workspace=case_workspace,
            candidate_path=candidate_paths[submitted_id],
            stop_time=float(case.get("final_stop_time") or 0.05),
            intervals=int(case.get("final_intervals") or 5),
            target_model_name=model_name,
            external_library_context=external_library_context,
        )
        final_policy_meta = _omc_policy_metadata(
            final_output,
            check_ok=bool(check_ok),
            simulate_ok=bool(simulate_ok),
        )
        final_simulation_status = str(final_policy_meta["simulation_status"])
        final_strict_simulate_ok = bool(final_policy_meta["strict_simulate_ok"])
        final_simulation_warning_markers = list(final_policy_meta["simulation_warning_markers"])
        final_verdict = "PASS" if final_policy_meta["policy_pass"] else "FAILED"
        steps.append(
            {
                "step": "final_eval",
                "candidate_id": submitted_id,
                "check_ok": bool(final_policy_meta["accepted_check_ok"]),
                "simulate_ok": bool(final_policy_meta["accepted_simulate_ok"]),
                "raw_check_ok": bool(check_ok),
                "raw_simulate_ok": bool(simulate_ok),
                "policy_pass": bool(final_policy_meta["policy_pass"]),
                "strict_simulate_ok": final_strict_simulate_ok,
                "simulation_status": final_simulation_status,
                "simulation_warning_markers": final_simulation_warning_markers,
                "omc_output": str(final_output or "")[:2000],
            }
        )
    _write_case_status(
        case_workspace,
        timeout_phase="case_completed",
        provider=provider,
        final_verdict=final_verdict,
        submitted_candidate_id=submitted_id,
        token_used=token_used,
    )
    candidate_files = list(candidate_meta.values())
    passing_candidate_ids = [
        str(candidate.get("candidate_id") or "")
        for candidate in candidate_files
        if candidate.get("write_check_ok") and candidate.get("write_simulate_ok")
    ]
    first_passing_candidate_id = passing_candidate_ids[0] if passing_candidate_ids else ""
    tool_call_counts: dict[str, int] = {}
    for step in steps:
        for call in step.get("tool_calls") or []:
            name = str(call.get("name") or "")
            if name:
                tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
    truncated_read_count = 0
    for step in steps:
        for result in step.get("tool_results") or []:
            if not isinstance(result, dict) or result.get("name") != "read_file":
                continue
            try:
                payload = json.loads(str(result.get("result") or ""))
            except json.JSONDecodeError:
                payload = {}
            if payload.get("truncated"):
                truncated_read_count += 1

    return {
        "case_id": case_id,
        "model_name": model_name,
        "provider": provider,
        "run_mode": "workspace_style_tool_use",
        "tool_count": len(WORKSPACE_TOOL_DEFS),
        "final_verdict": final_verdict,
        "final_simulation_status": final_simulation_status,
        "final_strict_simulate_ok": final_strict_simulate_ok,
        "final_simulation_warning_markers": final_simulation_warning_markers,
        "submitted": bool(submitted_id),
        "submitted_candidate_id": submitted_id,
        "submission_mode": submission_mode if submitted_id else "none",
        "step_count": len(steps),
        "token_used": token_used,
        "wall_time_sec": round(time.monotonic() - case_started_at, 3),
        "max_token_budget": int(max_token_budget),
        "token_budget_exceeded": bool(token_used >= max_token_budget),
        "budget_exceeded_at_step": budget_exceeded_at_step,
        "stop_reason": stop_reason,
        "provider_error": provider_error,
        "candidate_files": candidate_files,
        "candidate_count": len(candidate_files),
        "passing_candidate_ids": passing_candidate_ids,
        "first_passing_candidate_id": first_passing_candidate_id,
        "passing_candidate_count": len(passing_candidate_ids),
        "first_passing_candidate_after_budget": bool(first_passing_candidate_id and token_used >= max_token_budget and not submitted_id),
        "tool_call_counts": dict(sorted(tool_call_counts.items())),
        "truncated_read_count": truncated_read_count,
        "search_count": int(tool_call_counts.get("search_workspace_files", 0)),
        "slice_read_count": int(tool_call_counts.get("read_file_slice", 0)),
        "steps": steps,
        "final_model_text": final_model_text,
        "invalid_submission_attempt_count": len(invalid_submission_attempts),
        "invalid_submission_attempts": invalid_submission_attempts,
        "submit_checkpoint_triggered": False,
        "external_library_context_used": bool(external_library_context),
        "discipline": {
            "deterministic_repair_added": False,
            "hidden_routing_added": False,
            "candidate_selection_added": False,
            "wrapper_auto_submit_added": False,
            "llm_submit_required": True,
            "run_profile": RUN_PROFILE,
            "product_repair_profile": PRODUCT_REPAIR_PROFILE,
        },
    }


RunWorkspaceCaseFn = Callable[..., dict[str, Any]]


def _candidate_file_audit(
    case_workspace: Path,
    *,
    exclude_stems: set[str] | None = None,
) -> list[dict[str, Any]]:
    if not case_workspace.exists():
        return []
    excluded = set(exclude_stems or set())
    excluded.update(_safe_model_file_stem(stem) for stem in list(excluded))
    rows: list[dict[str, Any]] = []
    for path in sorted(case_workspace.glob("*.mo")):
        if path.name in {"initial.mo"}:
            continue
        if path.stem in excluded:
            continue
        text = path.read_text(encoding="utf-8")
        row = {
            "candidate_id": path.stem,
            "path": str(path),
            "model_name": _extract_model_name(text),
            "byte_count": len(text.encode("utf-8")),
        }
        omc_output_path = case_workspace / f"{path.stem}.omc.txt"
        if omc_output_path.exists():
            output = omc_output_path.read_text(encoding="utf-8", errors="replace")
            check_ok, simulate_ok = extract_om_success_flags(output)
            policy_meta = _omc_policy_metadata(
                output,
                check_ok=bool(check_ok),
                simulate_ok=bool(simulate_ok),
            )
            row.update(
                {
                    "write_check_ok": bool(policy_meta["accepted_check_ok"]),
                    "write_simulate_ok": bool(policy_meta["accepted_simulate_ok"]),
                    "write_raw_check_ok": bool(check_ok),
                    "write_raw_simulate_ok": bool(simulate_ok),
                    "write_policy_pass": bool(policy_meta["policy_pass"]),
                    "write_strict_simulate_ok": bool(policy_meta["strict_simulate_ok"]),
                    "write_simulation_status": str(policy_meta["simulation_status"]),
                    "write_simulation_warning_markers": list(policy_meta["simulation_warning_markers"]),
                    "omc_output_path": str(omc_output_path),
                }
            )
        rows.append(row)
    return rows


def _timeout_result(
    case: dict[str, Any],
    *,
    timeout_sec: int,
    out_dir: Path | None = None,
    max_token_budget: int = 0,
) -> dict[str, Any]:
    case_workspace = (
        (out_dir / "workspaces" / str(case.get("case_id") or "")).resolve() if out_dir else Path()
    )
    model_name = str(case.get("model_name") or "")
    status = _read_case_status(case_workspace) if out_dir else {}
    timeout_phase = str(status.get("timeout_phase") or "unknown")
    token_used = int(status.get("token_used") or 0)
    budget = int(max_token_budget or 0)
    candidate_files = _candidate_file_audit(case_workspace, exclude_stems={model_name}) if out_dir else []
    passing_candidate_ids = [
        str(candidate.get("candidate_id") or "")
        for candidate in candidate_files
        if candidate.get("write_check_ok") and candidate.get("write_simulate_ok")
    ]
    first_passing_candidate_id = passing_candidate_ids[0] if passing_candidate_ids else ""
    return {
        "case_id": str(case.get("case_id") or ""),
        "model_name": str(case.get("model_name") or ""),
        "provider": str(status.get("provider") or ""),
        "run_mode": "workspace_style_tool_use",
        "tool_count": len(WORKSPACE_TOOL_DEFS),
        "final_verdict": "FAILED_TIMEOUT",
        "submitted": False,
        "submitted_candidate_id": "",
        "step_count": int(status.get("step") or 0),
        "token_used": token_used,
        "provider_error": "",
        "harness_timeout": True,
        "timeout_sec": int(timeout_sec),
        "timeout_phase": timeout_phase,
        "timeout_status": status,
        "wall_time_sec": int(timeout_sec),
        "max_token_budget": budget,
        "token_budget_exceeded": bool(budget > 0 and token_used >= budget),
        "budget_exceeded_at_step": 0,
        "stop_reason": "harness_timeout",
        "candidate_count": len(candidate_files),
        "passing_candidate_ids": passing_candidate_ids,
        "first_passing_candidate_id": first_passing_candidate_id,
        "passing_candidate_count": len(passing_candidate_ids),
        "first_passing_candidate_after_budget": False,
        "tool_call_counts": {},
        "truncated_read_count": 0,
        "search_count": 0,
        "slice_read_count": 0,
        "candidate_files": candidate_files,
        "steps": [],
        "final_model_text": "",
        "submit_checkpoint_triggered": False,
        "submission_mode": "none",
        "discipline": {
            "deterministic_repair_added": False,
            "hidden_routing_added": False,
            "candidate_selection_added": False,
            "wrapper_auto_submit_added": False,
            "llm_submit_required": True,
            "run_profile": RUN_PROFILE,
            "product_repair_profile": PRODUCT_REPAIR_PROFILE,
        },
    }


def _case_worker(
    queue: mp.Queue,
    case: dict[str, Any],
    out_dir: str,
    max_steps: int,
    max_token_budget: int,
    planner_backend: str,
    run_case_fn: RunWorkspaceCaseFn,
) -> None:
    try:
        result = run_case_fn(
            case,
            out_dir=Path(out_dir),
            max_steps=max_steps,
            max_token_budget=max_token_budget,
            planner_backend=planner_backend,
        )
        queue.put({"ok": True, "result": _redact_result_for_artifact(result)})
    except Exception as exc:
        queue.put({"ok": False, "error": f"{type(exc).__name__}:{exc}"})


def _run_case_with_timeout(
    case: dict[str, Any],
    *,
    out_dir: Path,
    max_steps: int,
    max_token_budget: int,
    planner_backend: str,
    timeout_sec: int,
    run_case_fn: RunWorkspaceCaseFn,
) -> dict[str, Any]:
    if timeout_sec <= 0:
        return run_case_fn(
            case,
            out_dir=out_dir,
            max_steps=max_steps,
            max_token_budget=max_token_budget,
            planner_backend=planner_backend,
        )
    queue: mp.Queue = mp.Queue()
    proc = mp.Process(
        target=_case_worker,
        args=(queue, case, str(out_dir), max_steps, max_token_budget, planner_backend, run_case_fn),
    )
    proc.start()
    deadline = time.time() + max(1, int(timeout_sec))
    payload: dict[str, Any] | None = None
    while time.time() < deadline:
        try:
            if not queue.empty():
                payload = dict(queue.get(timeout=1))
                break
        except Exception:
            pass
        proc.join(timeout=1)
        if not proc.is_alive():
            break
    if proc.is_alive():
        if payload is None:
            try:
                if not queue.empty():
                    payload = dict(queue.get(timeout=1))
            except Exception:
                payload = None
        if payload is not None:
            proc.terminate()
            proc.join(timeout=5)
            if payload.get("ok"):
                result = dict(payload.get("result") or {})
                result["harness_timeout"] = False
                return result
            result = _timeout_result(case, timeout_sec=timeout_sec, out_dir=out_dir, max_token_budget=max_token_budget)
            result["final_verdict"] = "FAILED_RUNNER_ERROR"
            result["harness_timeout"] = False
            result["runner_error"] = str(payload.get("error") or "")
            return result
        proc.terminate()
        proc.join(timeout=5)
        return _timeout_result(case, timeout_sec=timeout_sec, out_dir=out_dir, max_token_budget=max_token_budget)
    if payload is None and not queue.empty():
        payload = dict(queue.get())
    if payload is None:
        result = _timeout_result(case, timeout_sec=timeout_sec, out_dir=out_dir, max_token_budget=max_token_budget)
        result["final_verdict"] = "FAILED_RUNNER_ERROR"
        result["harness_timeout"] = False
        result["runner_error"] = "subprocess_returned_no_result"
        return result
    if payload.get("ok"):
        result = dict(payload.get("result") or {})
        result["harness_timeout"] = False
        return result
    result = _timeout_result(case, timeout_sec=timeout_sec, out_dir=out_dir, max_token_budget=max_token_budget)
    result["final_verdict"] = "FAILED_RUNNER_ERROR"
    result["harness_timeout"] = False
    result["runner_error"] = str(payload.get("error") or "")
    return result


def load_holdout_tasks(path: Path = DEFAULT_TASKS) -> list[dict[str, Any]]:
    return sorted(
        [
            row for row in load_jsonl(path)
            if str(row.get("dataset_split") or "holdout") == "holdout"
        ],
        key=lambda row: str(row.get("case_id") or ""),
    )


def run_workspace_style_probe(
    *,
    tasks_path: Path = DEFAULT_TASKS,
    out_dir: Path = DEFAULT_OUT_DIR,
    case_ids: list[str] | None = None,
    limit: int = 0,
    max_steps: int = 10,
    max_token_budget: int = 32000,
    planner_backend: str = "auto",
    per_case_timeout_sec: int = 0,
    summary_version: str = "v0.67.0",
    run_profile: str = DEFAULT_RUN_PROFILE,
    run_case_fn: RunWorkspaceCaseFn = run_workspace_style_case,
) -> dict[str, Any]:
    wanted = set(case_ids or [])
    tasks = load_holdout_tasks(tasks_path)
    if wanted:
        tasks = [task for task in tasks if str(task.get("case_id") or "") in wanted]
    if limit:
        tasks = tasks[: max(0, int(limit))]
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"
    results_path.write_text("", encoding="utf-8")
    results: list[dict[str, Any]] = []
    (out_dir / "summary.json").write_text(
        json.dumps(
            _build_summary(
                tasks=tasks,
                results=[],
                summary_version=summary_version,
                max_token_budget=max_token_budget,
                run_profile=run_profile,
                max_steps=max_steps,
                per_case_timeout_sec=per_case_timeout_sec,
            ),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    for task in tasks:
        case = task_to_tool_use_case(task)
        if per_case_timeout_sec > 0:
            result = _run_case_with_timeout(
                case,
                out_dir=out_dir,
                max_steps=max_steps,
                max_token_budget=max_token_budget,
                planner_backend=planner_backend,
                timeout_sec=per_case_timeout_sec,
                run_case_fn=run_case_fn,
            )
        else:
            result = run_case_fn(
                case,
                out_dir=out_dir,
                max_steps=max_steps,
                max_token_budget=max_token_budget,
                planner_backend=planner_backend,
            )
        result = _redact_result_for_artifact(result)
        if result.get("final_verdict") == "PASS":
            behavioral = evaluate_optional_behavior(
                task, str(result.get("final_model_text") or "")
            )
            result["behavioral_eval"] = behavioral
            if not bool(behavioral.get("pass")):
                result["final_verdict"] = "FAILED_BEHAVIOR"
        else:
            result["behavioral_eval"] = {"pass": False, "reason": "skipped_after_structural_failure"}
        results.append(result)
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, sort_keys=True) + "\n")
        (out_dir / "summary.json").write_text(
            json.dumps(
                _build_summary(
                    tasks=tasks,
                    results=results,
                    summary_version=summary_version,
                    max_token_budget=max_token_budget,
                    run_profile=run_profile,
                    max_steps=max_steps,
                    per_case_timeout_sec=per_case_timeout_sec,
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    if not tasks:
        (out_dir / "summary.json").write_text(
            json.dumps(
                _build_summary(
                    tasks=[],
                    results=[],
                    summary_version=summary_version,
                    max_token_budget=max_token_budget,
                    run_profile=run_profile,
                    max_steps=max_steps,
                    per_case_timeout_sec=per_case_timeout_sec,
                ),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return load_json(out_dir / "summary.json")


def _build_summary(
    *,
    tasks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    summary_version: str = "v0.67.0",
    max_token_budget: int = 0,
    run_profile: str = DEFAULT_RUN_PROFILE,
    max_steps: int = 10,
    per_case_timeout_sec: int = 0,
) -> dict[str, Any]:
    provider_error_count = sum(1 for row in results if row.get("provider_error"))
    timeout_count = sum(1 for row in results if row.get("harness_timeout"))
    runner_error_count = sum(1 for row in results if row.get("runner_error"))
    pass_count = sum(1 for row in results if row.get("final_verdict") == "PASS")
    candidate_file_count = sum(len(row.get("candidate_files") or []) for row in results)
    invalid_submission_attempt_count = sum(
        int(row.get("invalid_submission_attempt_count") or 0) for row in results
    )
    truncated_read_count = sum(int(row.get("truncated_read_count") or 0) for row in results)
    search_count = sum(int(row.get("search_count") or 0) for row in results)
    slice_read_count = sum(int(row.get("slice_read_count") or 0) for row in results)
    checkpoint_triggered_count = sum(1 for row in results if row.get("submit_checkpoint_triggered"))
    checkpoint_pass_count = sum(
        1 for row in results
        if row.get("submit_checkpoint_triggered") and row.get("final_verdict") == "PASS"
    )
    llm_submitted_pass_count = sum(
        1 for row in results
        if row.get("submission_mode") == "llm" and row.get("final_verdict") == "PASS"
    )
    over_budget_case_ids = [
        str(row.get("case_id") or "")
        for row in results
        if max_token_budget > 0 and int(row.get("token_used") or 0) > max_token_budget
    ]
    over_budget_rows = [
        {
            "case_id": str(row.get("case_id") or ""),
            "token_used": int(row.get("token_used") or 0),
            "max_token_budget": int(max_token_budget),
            "token_overage": int(row.get("token_used") or 0) - int(max_token_budget),
            "token_overage_ratio": (
                round((int(row.get("token_used") or 0) - int(max_token_budget)) / int(max_token_budget), 4)
                if max_token_budget > 0
                else 0
            ),
        }
        for row in results
        if max_token_budget > 0 and int(row.get("token_used") or 0) > max_token_budget
    ]
    stop_reason_counts: dict[str, int] = {}
    for row in results:
        reason = str(row.get("stop_reason") or "")
        if reason:
            stop_reason_counts[reason] = stop_reason_counts.get(reason, 0) + 1
    wall_times = [float(row.get("wall_time_sec") or 0) for row in results if row.get("wall_time_sec") is not None]
    return {
        "version": summary_version,
        "analysis_scope": "workspace_style_probe_merged_tools",
        "status": "PASS" if tasks else "REVIEW",
        "evidence_role": "formal_experiment",
        "artifact_complete": len(results) == len(tasks),
        "conclusion_allowed": bool(
            tasks
            and len(results) == len(tasks)
            and provider_error_count == 0
            and timeout_count == 0
            and runner_error_count == 0
            and checkpoint_triggered_count == 0
        ),
        "run_mode": "workspace_style_tool_use",
        "run_profile": str(run_profile or DEFAULT_RUN_PROFILE),
        "max_steps": int(max_steps),
        "per_case_timeout_sec": int(per_case_timeout_sec or 0),
        "tool_count": len(WORKSPACE_TOOL_DEFS),
        "case_count": len(tasks),
        "completed_case_count": len(results),
        "pass_count": pass_count,
        "fail_count": len(results) - pass_count,
        "provider_error_count": provider_error_count,
        "harness_timeout_count": timeout_count,
        "runner_error_count": runner_error_count,
        "candidate_file_count": candidate_file_count,
        "truncated_read_count": truncated_read_count,
        "search_count": search_count,
        "slice_read_count": slice_read_count,
        "passing_candidate_case_ids": [
            str(row.get("case_id") or "")
            for row in results
            if int(row.get("passing_candidate_count") or 0) > 0
        ],
        "first_passing_candidate_after_budget_case_ids": [
            str(row.get("case_id") or "")
            for row in results
            if row.get("first_passing_candidate_after_budget")
        ],
        "invalid_submission_attempt_count": invalid_submission_attempt_count,
        "stop_reason_counts": dict(sorted(stop_reason_counts.items())),
        "wall_time_total_sec": round(sum(wall_times), 3),
        "wall_time_avg_sec": round(sum(wall_times) / len(wall_times), 3) if wall_times else 0,
        "max_token_budget": int(max_token_budget or 0),
        "over_token_budget_count": len(over_budget_case_ids),
        "over_token_budget_case_ids": over_budget_case_ids,
        "over_token_budget_rows": over_budget_rows,
        "submit_checkpoint_count": checkpoint_triggered_count,
        "submit_checkpoint_pass_count": checkpoint_pass_count,
        "llm_submitted_pass_count": llm_submitted_pass_count,
        "non_llm_submitted_pass_count": pass_count - llm_submitted_pass_count,
        "case_ids": [str(task.get("case_id") or "") for task in tasks],
        "completed_case_ids": [str(row.get("case_id") or "") for row in results],
        "pass_case_ids": [
            str(row.get("case_id") or "") for row in results if row.get("final_verdict") == "PASS"
        ],
        "fail_case_ids": [
            str(row.get("case_id") or "") for row in results if row.get("final_verdict") != "PASS"
        ],
        "discipline": {
            "deterministic_repair_added": False,
            "hidden_routing_added": False,
            "candidate_selection_added": False,
            "wrapper_auto_submit_added": False,
            "llm_submit_required": True,
            "live_submit_checkpoint_removed": True,
            "transparent_workspace_enabled": True,
            "merged_write_check_tool": True,
            "run_profile": RUN_PROFILE,
            "product_repair_profile": PRODUCT_REPAIR_PROFILE,
        },
    }
