#!/usr/bin/env python3
"""Run an experiment and always remove its regenerable artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gateforge.experiment_artifact_cleanup_v1 import (  # noqa: E402
    run_command_with_cleanup,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    return_code, report = run_command_with_cleanup(
        command, artifact_root=args.artifact_root,
    )
    if report.status != "PASS" and return_code == 0:
        return 73
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
