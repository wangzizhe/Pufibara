"""Conservative cleanup for regenerable experiment artifacts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


BUILD_SUFFIXES = {
    ".a", ".bin", ".c", ".dylib", ".h", ".libs", ".makefile",
    ".o", ".so",
}
DISPOSABLE_DIRECTORY_NAMES = {".omc_home", "__pycache__"}
DISPOSABLE_FILE_NAMES = {"run.mos"}
DIRECTORY_CLEANUP_RETRY_DELAYS_SEC = (0.1, 0.25, 0.5, 1.0)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CleanupReport:
    scanned_directories: int = 0
    candidate_files: int = 0
    deleted_files: int = 0
    deleted_directories: int = 0
    deleted_bytes: int = 0
    failed_paths: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "PASS" if not self.failed_paths else "REVIEW"

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, **asdict(self)}


def _safe_root(root: str | Path) -> Path:
    path = Path(root)
    if path.is_symlink():
        raise ValueError("cleanup_root_cannot_be_symlink")
    resolved = path.resolve()
    forbidden = {
        Path(resolved.anchor),
        Path.home().resolve(),
        Path(tempfile.gettempdir()).resolve(),
        REPOSITORY_ROOT,
        REPOSITORY_ROOT / "artifacts",
    }
    if resolved in forbidden:
        raise ValueError("cleanup_root_is_too_broad")
    return resolved


def _generated_stems(directory: Path) -> set[str]:
    stems: set[str] = set()
    for path in directory.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        name = path.name
        if name.endswith(".makefile"):
            stems.add(name[: -len(".makefile")])
        elif name.endswith("_init.xml"):
            stems.add(name[: -len("_init.xml")])
        elif name.endswith("_info.json"):
            stems.add(name[: -len("_info.json")])
    return stems


def _is_generated_build_file(path: Path, stems: set[str]) -> bool:
    name = path.name
    for stem in stems:
        associated = (
            name == stem
            or name == stem + ".exe"
            or name.startswith(stem + ".")
            or name.startswith(stem + "_")
        )
        if not associated:
            continue
        if name in {stem + "_init.xml", stem + "_info.json"}:
            return True
        if name in {stem, stem + ".exe"}:
            return True
        return path.suffix in BUILD_SUFFIXES
    return False


def _remove_directory_with_retry(path: Path) -> None:
    """Handle short Docker Desktop bind-unmount races without hiding failure."""

    for attempt in range(len(DIRECTORY_CLEANUP_RETRY_DELAYS_SEC) + 1):
        try:
            shutil.rmtree(path)
            return
        except OSError:
            if not path.exists():
                return
            if attempt >= len(DIRECTORY_CLEANUP_RETRY_DELAYS_SEC):
                raise
            time.sleep(DIRECTORY_CLEANUP_RETRY_DELAYS_SEC[attempt])


def cleanup_omc_build_byproducts(
    workspace: str | Path,
    *,
    dry_run: bool = False,
) -> CleanupReport:
    """Delete only top-level OMC build products, preserving evidence files."""

    root = _safe_root(workspace)
    if not root.exists():
        return CleanupReport()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    stems = _generated_stems(root)
    candidates = [
        path
        for path in root.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and (
            _is_generated_build_file(path, stems)
            or path.name in DISPOSABLE_FILE_NAMES
        )
    ]
    deleted_files = 0
    deleted_directories = 0
    deleted_bytes = 0
    failed: list[str] = []
    for path in candidates:
        try:
            size = path.stat().st_size
            if not dry_run:
                path.unlink()
            deleted_files += 1
            deleted_bytes += size
        except OSError:
            failed.append(str(path))

    for name in DISPOSABLE_DIRECTORY_NAMES:
        path = root / name
        if not path.exists() or path.is_symlink():
            continue
        try:
            size = sum(
                child.stat().st_size
                for child in path.rglob("*")
                if child.is_file() and not child.is_symlink()
            )
            if not dry_run:
                _remove_directory_with_retry(path)
            deleted_directories += 1
            deleted_bytes += size
        except OSError:
            failed.append(str(path))

    return CleanupReport(
        scanned_directories=1,
        candidate_files=len(candidates),
        deleted_files=deleted_files,
        deleted_directories=deleted_directories,
        deleted_bytes=deleted_bytes,
        failed_paths=tuple(sorted(failed)),
    )


def cleanup_experiment_tree(
    artifact_root: str | Path,
    *,
    dry_run: bool = False,
) -> CleanupReport:
    """Clean OMC products and caches below one explicit experiment root."""

    root = _safe_root(artifact_root)
    if not root.exists():
        return CleanupReport()
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    totals = {
        "scanned_directories": 0,
        "candidate_files": 0,
        "deleted_files": 0,
        "deleted_directories": 0,
        "deleted_bytes": 0,
    }
    failed: list[str] = []
    for current, directory_names, file_names in os.walk(root, topdown=True):
        directory_names[:] = [
            name for name in directory_names
            if not (Path(current) / name).is_symlink()
        ]
        directory = Path(current)
        report = cleanup_omc_build_byproducts(directory, dry_run=dry_run)
        for key in totals:
            totals[key] += int(getattr(report, key))
        failed.extend(report.failed_paths)
        for name in file_names:
            if not name.endswith(".pyc"):
                continue
            path = directory / name
            if path.is_symlink():
                continue
            try:
                size = path.stat().st_size
                if not dry_run:
                    path.unlink()
                totals["candidate_files"] += 1
                totals["deleted_files"] += 1
                totals["deleted_bytes"] += size
            except OSError:
                failed.append(str(path))
        directory_names[:] = [
            name for name in directory_names
            if name not in DISPOSABLE_DIRECTORY_NAMES
        ]

    return CleanupReport(**totals, failed_paths=tuple(sorted(set(failed))))


def write_cleanup_summary(path: str | Path, report: CleanupReport) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def run_command_with_cleanup(
    command: Sequence[str],
    *,
    artifact_root: str | Path,
    environment: Mapping[str, str] | None = None,
) -> tuple[int, CleanupReport]:
    """Run one command and clean its explicit artifact root on every exit."""

    if not command:
        raise ValueError("experiment_command_required")
    root = _safe_root(artifact_root)
    child_environment = dict(os.environ)
    if environment is not None:
        child_environment.update({str(k): str(v) for k, v in environment.items()})
    child_environment["GATEFORGE_EXPERIMENT_ARTIFACT_ROOT"] = str(root)
    return_code = 1
    try:
        return_code = subprocess.run(
            list(command), env=child_environment, check=False,
        ).returncode
    finally:
        report = cleanup_experiment_tree(root)
        write_cleanup_summary(root / "artifact_cleanup_summary.json", report)
    return int(return_code), report
