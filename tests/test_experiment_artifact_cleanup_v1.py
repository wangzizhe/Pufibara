from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gateforge.agent_modelica_omc_workspace_v1 import run_omc_script_docker
from gateforge.experiment_artifact_cleanup_v1 import (
    cleanup_experiment_tree,
    cleanup_omc_build_byproducts,
    run_command_with_cleanup,
)


class ExperimentArtifactCleanupTests(unittest.TestCase):
    def test_build_cleanup_preserves_evidence_and_unrelated_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            preserved = {
                "ModelA.mo": "model ModelA end ModelA;",
                "ModelA_res.mat": "result",
                "omc_output.txt": "output",
                "LibraryRuntime.c": "source",
            }
            generated = {
                "ModelA.makefile": "make",
                "ModelA.c": "source",
                "ModelA_01exo.c": "source",
                "ModelA.h": "header",
                "ModelA.o": "object",
                "ModelA_init.xml": "metadata",
                "ModelA_info.json": "{}",
                "ModelA": "binary",
                "run.mos": "simulate(ModelA);",
            }
            for name, content in {**preserved, **generated}.items():
                (root / name).write_text(content, encoding="utf-8")
            (root / ".omc_home").mkdir()
            (root / ".omc_home" / "cache").write_text(
                "cache", encoding="utf-8"
            )

            report = cleanup_omc_build_byproducts(root)

            self.assertEqual(report.status, "PASS")
            self.assertGreater(report.deleted_bytes, 0)
            for name in preserved:
                self.assertTrue((root / name).exists(), name)
            for name in generated:
                self.assertFalse((root / name).exists(), name)
            self.assertFalse((root / ".omc_home").exists())

    def test_tree_cleanup_removes_nested_build_products_and_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "experiment"
            workspace = root / "verification" / "docker_omc" / "case"
            workspace.mkdir(parents=True)
            for name in (
                "ModelA.makefile",
                "ModelA.c",
                "ModelA.o",
                "ModelA_init.xml",
                "ModelA_info.json",
            ):
                (workspace / name).write_text("generated", encoding="utf-8")
            (workspace / "ModelA.mo").write_text("model", encoding="utf-8")
            (workspace / "ModelA_res.mat").write_text(
                "result", encoding="utf-8"
            )
            cache = root / "__pycache__"
            cache.mkdir()
            (cache / "module.pyc").write_bytes(b"cache")

            report = cleanup_experiment_tree(root)

            self.assertEqual(report.status, "PASS")
            self.assertTrue((workspace / "ModelA.mo").exists())
            self.assertTrue((workspace / "ModelA_res.mat").exists())
            self.assertFalse((workspace / "ModelA.o").exists())
            self.assertFalse(cache.exists())

    def test_directory_cleanup_retries_transient_unmount_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disposable = root / ".omc_home"
            disposable.mkdir()
            (disposable / "cache").write_text("cache", encoding="utf-8")
            real_rmtree = __import__("shutil").rmtree
            calls = 0

            def flaky_rmtree(path):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise OSError("transient bind unmount")
                real_rmtree(path)

            with mock.patch(
                "gateforge.experiment_artifact_cleanup_v1.shutil.rmtree",
                side_effect=flaky_rmtree,
            ), mock.patch(
                "gateforge.experiment_artifact_cleanup_v1.time.sleep"
            ) as sleep:
                report = cleanup_omc_build_byproducts(root)

            self.assertEqual(report.status, "PASS")
            self.assertEqual(calls, 2)
            sleep.assert_called_once_with(0.1)
            self.assertFalse(disposable.exists())

    def test_directory_cleanup_reports_persistent_failure_after_retries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            disposable = root / ".omc_home"
            disposable.mkdir()
            with mock.patch(
                "gateforge.experiment_artifact_cleanup_v1.shutil.rmtree",
                side_effect=OSError("persistent permission failure"),
            ) as remove, mock.patch(
                "gateforge.experiment_artifact_cleanup_v1.time.sleep"
            ):
                report = cleanup_omc_build_byproducts(root)

            self.assertEqual(report.status, "REVIEW")
            self.assertEqual(report.failed_paths, (str(disposable.resolve()),))
            self.assertEqual(remove.call_count, 5)

    def test_command_cleanup_runs_after_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "experiment"
            workspace = root / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "ModelA.makefile").write_text(
                "make", encoding="utf-8"
            )
            (workspace / "ModelA.o").write_text("object", encoding="utf-8")

            return_code, report = run_command_with_cleanup(
                [sys.executable, "-c", "raise SystemExit(7)"],
                artifact_root=root,
            )

            self.assertEqual(return_code, 7)
            self.assertEqual(report.status, "PASS")
            self.assertFalse((workspace / "ModelA.o").exists())
            summary = json.loads(
                (root / "artifact_cleanup_summary.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(summary["status"], "PASS")

    def test_docker_runner_always_cleans_after_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fake_run(*args, **kwargs):
                del args, kwargs
                (root / "ModelA.makefile").write_text("make", encoding="utf-8")
                (root / "ModelA.o").write_text("object", encoding="utf-8")
                (root / "ModelA_res.mat").write_text("result", encoding="utf-8")
                return 0, "ok"

            with mock.patch.dict(
                "os.environ",
                {"GATEFORGE_OM_DOCKER_LIBRARY_CACHE": str(root / "cache")},
                clear=False,
            ), mock.patch(
                "gateforge.agent_modelica_omc_workspace_v1.run_cmd",
                side_effect=fake_run,
            ):
                result = run_omc_script_docker(
                    "simulate(ModelA);", 10, str(root), "fixture-image"
                )

            self.assertEqual(result, (0, "ok"))
            self.assertFalse((root / "ModelA.o").exists())
            self.assertFalse((root / "ModelA.makefile").exists())
            self.assertTrue((root / "ModelA_res.mat").exists())

    def test_docker_runner_reports_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            failed_report = mock.Mock(
                status="REVIEW", failed_paths=(str(root / "ModelA.o"),)
            )
            with mock.patch.dict(
                "os.environ",
                {"GATEFORGE_OM_DOCKER_LIBRARY_CACHE": str(root / "cache")},
                clear=False,
            ), mock.patch(
                "gateforge.agent_modelica_omc_workspace_v1.run_cmd",
                return_value=(0, "ok"),
            ), mock.patch(
                "gateforge.agent_modelica_omc_workspace_v1.cleanup_omc_build_byproducts",
                return_value=failed_report,
            ):
                with self.assertRaisesRegex(RuntimeError, "cleanup_failed"):
                    run_omc_script_docker(
                        "simulate(ModelA);", 10, str(root), "fixture-image"
                    )

    def test_rejects_broad_or_symlink_roots(self):
        with self.assertRaisesRegex(ValueError, "too_broad"):
            cleanup_experiment_tree(Path.home())
        repository_root = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(ValueError, "too_broad"):
            cleanup_experiment_tree(repository_root)
        with self.assertRaisesRegex(ValueError, "too_broad"):
            cleanup_experiment_tree(repository_root / "artifacts")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            link = root / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                cleanup_experiment_tree(link)
