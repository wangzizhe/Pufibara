"""OMC workspace management for the GateForge Modelica agent.

All subprocess, Docker, and filesystem operations are isolated here so higher
layers can be tested without real OMC or Docker.

Public API (re-exported with ``_`` prefix in the executor for zero
call-site changes):

- :class:`WorkspaceModelLayout` – dataclass returned by workspace setup
- :func:`norm_path_text` – strip / coerce path-like strings
- :func:`rel_mos_path` – make a path relative for ``.mos`` scripts
- :func:`copytree_best_effort` – shutil.copytree that never raises
- :func:`prepare_workspace_model_layout` – set up model + library layout
- :func:`run_cmd` – low-level subprocess runner with timeout
- :func:`run_omc_script_local` – run a ``.mos`` script via local ``omc``
- :func:`run_omc_script_docker` – run a ``.mos`` script via Docker OMC
- :func:`extract_om_success_flags` – parse OMC output → (check_ok, sim_ok)
- :func:`classify_failure` – map OMC output → (error_type, reason)
- :func:`run_check_and_simulate` – orchestrate check + simulate in one call
- :func:`temporary_workspace` – context manager for an isolated temp dir
- :func:`cleanup_workspace_best_effort` – rmtree that never propagates errors
- :func:`_cleanup_stale_workspaces` – delete workspace dirs older than N hours (called on startup)
"""
from __future__ import annotations

import os
import re
import json
import shutil
import atexit
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .experiment_artifact_cleanup_v1 import cleanup_omc_build_byproducts


# ---------------------------------------------------------------------------
# Docker container lifecycle cleanup
# ---------------------------------------------------------------------------

_GATEFORGE_DOCKER_LABEL = "gateforge.omc=1"
_cleanup_registered = False


def _cleanup_omc_containers() -> None:
    """Stop and remove all Docker containers tagged with the GateForge label."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label={_GATEFORGE_DOCKER_LABEL}"],
            capture_output=True, text=True, timeout=10,
        )
        container_ids = result.stdout.strip().split()
        if container_ids:
            subprocess.run(
                ["docker", "stop", "--time", "5"] + container_ids,
                capture_output=True, timeout=30,
            )
            subprocess.run(
                ["docker", "rm", "-f"] + container_ids,
                capture_output=True, timeout=15,
            )
    except Exception:
        pass


def _sigterm_handler(signum: int, frame: object) -> None:
    _cleanup_omc_containers()
    raise SystemExit(128 + signum)


def _cleanup_stale_workspaces(max_age_hours: float = 4.0) -> None:
    """Delete workspace directories older than *max_age_hours* from the tmp root.

    Handles the case where a previous process was killed with SIGKILL and the
    ``temporary_workspace`` finally-block never ran.
    """
    root = _workspace_tmp_root()
    if root is None or not root.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for entry in root.iterdir():
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except Exception:
            pass


def _register_omc_cleanup_once() -> None:
    global _cleanup_registered
    if _cleanup_registered:
        return
    _cleanup_registered = True
    _cleanup_stale_workspaces()
    atexit.register(_cleanup_omc_containers)
    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (OSError, ValueError):
        pass

from .agent_modelica_diagnostic_ir_v0 import build_diagnostic_ir_v0


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class WorkspaceModelLayout:
    """Describes how a model (and optional library) is laid out in a workspace."""

    model_write_path: Path
    model_load_files: list[str]
    model_identifier: str
    uses_external_library: bool


# ---------------------------------------------------------------------------
# Path helpers (pure, no I/O)
# ---------------------------------------------------------------------------


def norm_path_text(value: str) -> str:
    """Strip and coerce a path-like string; return ``""`` for falsy input."""
    return str(value or "").strip()


def rel_mos_path(path: Path, workspace: Path) -> str:
    """Return *path* relative to *workspace* with forward slashes for ``.mos`` scripts."""
    rel = path.relative_to(workspace)
    return str(rel).replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def copytree_best_effort(src: Path, dst: Path) -> bool:
    """Copy *src* to *dst* recursively; return ``False`` on any error."""
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True)
        return True
    except Exception:
        return False


def prepare_workspace_model_layout(
    *,
    workspace: Path,
    fallback_model_path: Path,
    primary_model_name: str,
    source_library_path: str = "",
    source_package_name: str = "",
    source_library_model_path: str = "",
    source_qualified_model_name: str = "",
) -> WorkspaceModelLayout:
    """Set up the workspace directory for a model (with optional library).

    When *source_library_path*, *source_package_name*, and
    *source_qualified_model_name* are all provided the library is mirrored
    into the workspace so OMC can load it together with the mutated model.
    Otherwise only the single model file is written.
    """
    package_root_text = norm_path_text(source_library_path)
    package_name = norm_path_text(source_package_name)
    qualified_model_name = norm_path_text(source_qualified_model_name)
    source_model_in_library_text = norm_path_text(source_library_model_path)

    if package_root_text and package_name and qualified_model_name:
        package_root = Path(package_root_text)
        package_dir_name = package_name.split(".", 1)[0].strip() or package_root.name
        package_mirror = workspace / package_dir_name
        package_mirror_parent = package_mirror.parent
        package_mirror_parent.mkdir(parents=True, exist_ok=True)
        if package_root.exists() and copytree_best_effort(package_root, package_mirror):
            source_model_in_library = (
                Path(source_model_in_library_text) if source_model_in_library_text else None
            )
            if source_model_in_library is not None:
                try:
                    rel_model_path = source_model_in_library.relative_to(package_root)
                except Exception:
                    rel_model_path = Path(fallback_model_path.name)
            else:
                rel_model_path = Path(fallback_model_path.name)
            model_write_path = package_mirror / rel_model_path
            model_write_path.parent.mkdir(parents=True, exist_ok=True)
            load_files: list[str] = []
            package_file = package_mirror / "package.mo"
            if package_file.exists():
                load_files.append(rel_mos_path(package_file, workspace))
            return WorkspaceModelLayout(
                model_write_path=model_write_path,
                model_load_files=load_files + [rel_mos_path(model_write_path, workspace)],
                model_identifier=qualified_model_name,
                uses_external_library=True,
            )

    model_write_path = workspace / fallback_model_path.name
    return WorkspaceModelLayout(
        model_write_path=model_write_path,
        model_load_files=[rel_mos_path(model_write_path, workspace)],
        model_identifier=primary_model_name,
        uses_external_library=False,
    )


# ---------------------------------------------------------------------------
# Subprocess runner
# ---------------------------------------------------------------------------


def run_cmd(cmd: list[str], timeout_sec: int, cwd: str | None = None) -> tuple[int | None, str]:
    """Run *cmd* as a subprocess; return (returncode, merged stdout+stderr).

    Returns ``(None, "TimeoutExpired")`` on timeout and
    ``(None, "<ExcType>:<msg>")`` on other errors.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_sec)),
            check=False,
            cwd=cwd,
        )
        merged = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return int(proc.returncode), merged
    except subprocess.TimeoutExpired:
        return None, "TimeoutExpired"
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"{type(exc).__name__}:{exc}"


# ---------------------------------------------------------------------------
# OMC script runners
# ---------------------------------------------------------------------------


def run_omc_script_local(script_text: str, timeout_sec: int, cwd: str) -> tuple[int | None, str]:
    """Write *script_text* to ``run.mos`` and execute it via the local ``omc`` binary."""
    _register_omc_cleanup_once()
    script_path = Path(cwd) / "run.mos"
    script_path.write_text(script_text, encoding="utf-8")
    return run_cmd(["omc", str(script_path.name)], timeout_sec=timeout_sec, cwd=cwd)


def run_omc_script_docker(
    script_text: str,
    timeout_sec: int,
    cwd: str,
    image: str,
) -> tuple[int | None, str]:
    """Write *script_text* to ``run.mos`` and run it inside a Docker OMC container.

    The container is started with the current host user (``uid:gid``) so that
    all files written into the mounted workspace are user-owned and can be
    cleaned up with :func:`cleanup_workspace_best_effort` without elevated
    privileges.

    Library cache is mounted from ``GATEFORGE_OM_DOCKER_LIBRARY_CACHE`` (env)
    or ``~/.openmodelica/libraries``.
    """
    script_path = Path(cwd) / "run.mos"
    script_path.write_text(script_text, encoding="utf-8")
    cache_root_raw = str(os.getenv("GATEFORGE_OM_DOCKER_LIBRARY_CACHE") or "").strip()
    cache_root = (
        Path(cache_root_raw)
        if cache_root_raw
        else (Path.home() / ".openmodelica" / "libraries")
    )
    if not cache_root.is_absolute():
        cache_root = (Path(cwd) / cache_root).resolve()
    else:
        cache_root = cache_root.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    # Run as the current host user so all files written into the workspace are
    # user-owned. HOME is set to /workspace/.omc_home (pre-created below).
    # Mounting the library cache at /workspace/.omc_home/.openmodelica/libraries
    # avoids shadowing conflicts with the pre-created .omc_home/ directory.
    omc_home_host = Path(cwd) / ".omc_home"
    (omc_home_host / ".openmodelica" / "cache").mkdir(parents=True, exist_ok=True)
    uid_gid = f"{os.getuid()}:{os.getgid()}"
    _register_omc_cleanup_once()
    cmd = [
        "docker",
        "run",
        "--rm",
        "--label", _GATEFORGE_DOCKER_LABEL,
        "--user", uid_gid,
        "-e", "HOME=/workspace/.omc_home",
        "-v", f"{cwd}:/workspace",
        "-v", f"{str(cache_root)}:/workspace/.omc_home/.openmodelica/libraries",
        "-w", "/workspace",
        image,
        "omc",
        "run.mos",
    ]
    try:
        return run_cmd(cmd, timeout_sec=timeout_sec)
    finally:
        cleanup_report = cleanup_omc_build_byproducts(cwd)
        if cleanup_report.status != "PASS":
            raise RuntimeError(
                "omc_build_cleanup_failed:"
                + ",".join(cleanup_report.failed_paths)
            )


# ---------------------------------------------------------------------------
# OMC output parsing (pure functions — no I/O)
# ---------------------------------------------------------------------------


def extract_om_success_flags(output: str) -> tuple[bool, bool]:
    """Parse OMC stdout/stderr and return ``(check_ok, simulate_ok)``.

    Both flags are ``False`` on empty or error output.  The function is a
    pure text parser: it does not invoke OMC itself.
    """
    lower = str(output or "").lower()
    structural_mismatch = re.search(
        r"class\s+[a-z_][a-z0-9_]*\s+has\s+([0-9]+)\s+equation\(s\)\s+and\s+([0-9]+)\s+variable\(s\)",
        lower,
    )
    structural_balance_ok = True
    if structural_mismatch:
        try:
            structural_balance_ok = int(structural_mismatch.group(1)) == int(
                structural_mismatch.group(2)
            )
        except Exception:
            structural_balance_ok = True
    check_ok = (
        "check of" in lower
        and "completed successfully" in lower
        and structural_balance_ok
    )
    has_sim_result = "record simulationresult" in lower
    result_file_empty = 'resultfile = ""' in lower
    sim_error_markers = (
        "simulation execution failed" in lower
        or "error occurred while solving" in lower
        or "division by zero" in lower
        or "assertion" in lower
        or "integrator failed" in lower
    )
    simulate_ok = has_sim_result and not result_file_empty and not sim_error_markers
    return check_ok, simulate_ok


def classify_failure(
    output: str,
    check_ok: bool,
    simulate_ok: bool,
) -> tuple[str, str]:
    """Map OMC output to ``(error_type, reason)`` strings via the diagnostic IR."""
    diag = build_diagnostic_ir_v0(
        output=output,
        check_model_pass=bool(check_ok),
        simulate_pass=bool(simulate_ok),
        expected_stage="",
        declared_failure_type="",
    )
    return str(diag.get("error_type") or "none"), str(diag.get("reason") or "")


# ---------------------------------------------------------------------------
# High-level check + simulate orchestration
# ---------------------------------------------------------------------------


def run_check_and_simulate(
    *,
    workspace: Path,
    model_load_files: list[str],
    model_name: str,
    timeout_sec: int,
    backend: str,
    docker_image: str,
    stop_time: float,
    intervals: int,
    method: str = "",
    extra_model_loads: list[str] | None = None,
) -> tuple[int | None, str, bool, bool]:
    """Compile *and* simulate a model; return ``(rc, output, check_ok, simulate_ok)``.

    Selects the local ``omc`` binary when *backend* is ``"omc"``, otherwise
    uses the Docker runner.

    *extra_model_loads* is an optional list of Modelica package names to load
    after the standard ``loadModel(Modelica)`` bootstrap, e.g. ``["AixLib"]``
    for models that depend on an external library available in the OM library cache.
    """
    bootstrap = "loadModel(Modelica);\n"
    if extra_model_loads:
        bootstrap += "".join(f"loadModel({m});\n" for m in extra_model_loads if str(m or "").strip())
    load_lines = "".join(
        [f'loadFile("{item}");\n' for item in model_load_files if str(item or "").strip()]
    )
    method_text = str(method or "").strip()
    solver_arg = f", method={json.dumps(method_text)}" if method_text else ""
    script = (
        bootstrap
        + load_lines
        + f"checkModel({model_name});\n"
        + f"simulate({model_name}, stopTime={float(stop_time)}, numberOfIntervals={int(intervals)}{solver_arg});\n"
        + "getErrorString();\n"
    )
    if backend == "omc":
        rc, output = run_omc_script_local(script, timeout_sec=timeout_sec, cwd=str(workspace))
    else:
        rc, output = run_omc_script_docker(
            script, timeout_sec=timeout_sec, cwd=str(workspace), image=docker_image
        )
    check_ok, simulate_ok = extract_om_success_flags(output)
    return rc, output, check_ok, simulate_ok


# ---------------------------------------------------------------------------
# Temporary workspace lifecycle
# ---------------------------------------------------------------------------


def _workspace_tmp_root() -> Path | None:
    """Return the preferred root directory for temporary OMC workspaces.

    Priority:
    1. ``GATEFORGE_AGENT_TMP_ROOT`` if set.
    2. Repo-local ``tmp/docker`` when running inside the GateForge repo.
    3. ``None`` -> fall back to the system temp dir.
    """
    env_root = str(os.getenv("GATEFORGE_AGENT_TMP_ROOT") or "").strip()
    if env_root:
        root = Path(env_root).expanduser()
        if not root.is_absolute():
            root = root.resolve()
        return root

    repo_root = Path(__file__).resolve().parents[1]
    if (repo_root / ".git").exists():
        return repo_root / "tmp" / "docker"
    return None


@contextmanager
def temporary_workspace(prefix: str):
    """Context manager that creates and cleans up an isolated temp directory.

    Docker may write root-owned files into the mounted workspace, so
    :class:`tempfile.TemporaryDirectory` cleanup can raise
    :exc:`PermissionError` on CI.  This implementation uses :func:`os.mkdtemp`
    with a best-effort cleanup that never propagates teardown failures.
    """
    root = _workspace_tmp_root()
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
        td = tempfile.mkdtemp(prefix=prefix, dir=str(root))
    else:
        td = tempfile.mkdtemp(prefix=prefix)
    try:
        yield td
    finally:
        cleanup_workspace_best_effort(td)


def cleanup_workspace_best_effort(path: str) -> None:
    """Remove *path* recursively; silently ignore all errors."""
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        return
