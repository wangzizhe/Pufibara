"""Tests for agent_modelica_omc_workspace_v1.

Covers the pure / filesystem-only functions.  Docker-dependent functions
(_run_omc_script_docker, run_check_and_simulate with Docker backend) are
excluded — they are exercised by the pre-existing Docker integration tests.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gateforge.agent_modelica_omc_workspace_v1 import (
    WorkspaceModelLayout,
    _workspace_tmp_root,
    classify_failure,
    cleanup_workspace_best_effort,
    copytree_best_effort,
    extract_om_success_flags,
    norm_path_text,
    prepare_workspace_model_layout,
    rel_mos_path,
    run_check_and_simulate,
    temporary_workspace,
)


# ---------------------------------------------------------------------------
# norm_path_text
# ---------------------------------------------------------------------------


class TestNormPathText(unittest.TestCase):
    def test_strips_whitespace(self):
        self.assertEqual(norm_path_text("  /foo/bar  "), "/foo/bar")

    def test_empty_string(self):
        self.assertEqual(norm_path_text(""), "")

    def test_none_like_falsy(self):
        self.assertEqual(norm_path_text(""), "")

    def test_plain_string(self):
        self.assertEqual(norm_path_text("Buildings"), "Buildings")


# ---------------------------------------------------------------------------
# rel_mos_path
# ---------------------------------------------------------------------------


class TestRelMosPath(unittest.TestCase):
    def test_simple_relative(self):
        workspace = Path("/tmp/ws")
        path = Path("/tmp/ws/model.mo")
        self.assertEqual(rel_mos_path(path, workspace), "model.mo")

    def test_subdirectory(self):
        workspace = Path("/tmp/ws")
        path = Path("/tmp/ws/Lib/Sub/Model.mo")
        self.assertEqual(rel_mos_path(path, workspace), "Lib/Sub/Model.mo")

    def test_uses_forward_slash(self):
        workspace = Path("/tmp/ws")
        path = Path("/tmp/ws/A/B.mo")
        result = rel_mos_path(path, workspace)
        self.assertNotIn("\\", result)
        self.assertEqual(result, "A/B.mo")


# ---------------------------------------------------------------------------
# extract_om_success_flags
# ---------------------------------------------------------------------------


class TestExtractOmSuccessFlags(unittest.TestCase):
    def _check_flag_output(self):
        return (
            "Check of ModelA completed successfully.\n"
            "0 errors found.\n"
        )

    def _sim_success_output(self):
        return (
            self._check_flag_output()
            + 'record SimulationResult\n'
            + '  resultFile = "ModelA_res.mat",\n'
            + '  simulationOptions = "...",\n'
            + 'end SimulationResult;\n'
        )

    def test_both_pass(self):
        check_ok, sim_ok = extract_om_success_flags(self._sim_success_output())
        self.assertTrue(check_ok)
        self.assertTrue(sim_ok)

    def test_check_pass_sim_fail_empty_result(self):
        output = self._check_flag_output() + 'record SimulationResult\n  resultFile = "",\nend SimulationResult;\n'
        check_ok, sim_ok = extract_om_success_flags(output)
        self.assertTrue(check_ok)
        self.assertFalse(sim_ok)

    def test_check_fail_no_keyword(self):
        check_ok, sim_ok = extract_om_success_flags("Error: something went wrong")
        self.assertFalse(check_ok)
        self.assertFalse(sim_ok)

    def test_empty_output(self):
        check_ok, sim_ok = extract_om_success_flags("")
        self.assertFalse(check_ok)
        self.assertFalse(sim_ok)

    def test_simulation_execution_failed(self):
        output = self._check_flag_output() + 'record SimulationResult\n  resultFile = "r.mat",\nsimulation execution failed\nend SimulationResult;\n'
        check_ok, sim_ok = extract_om_success_flags(output)
        self.assertTrue(check_ok)
        self.assertFalse(sim_ok)

    def test_division_by_zero_fails_sim(self):
        output = self._check_flag_output() + 'record SimulationResult\n  resultFile = "r.mat",\ndivision by zero\nend SimulationResult;\n'
        _, sim_ok = extract_om_success_flags(output)
        self.assertFalse(sim_ok)

    def test_structural_mismatch_fails_check(self):
        # 3 equations, 4 variables → not balanced
        output = (
            "class ModelX has 3 equation(s) and 4 variable(s)\n"
            "Check of ModelX completed successfully.\n"
        )
        check_ok, _ = extract_om_success_flags(output)
        self.assertFalse(check_ok)

    def test_structural_balance_passes_check(self):
        output = (
            "class ModelX has 4 equation(s) and 4 variable(s)\n"
            "Check of ModelX completed successfully.\n"
        )
        check_ok, _ = extract_om_success_flags(output)
        self.assertTrue(check_ok)

    def test_case_insensitive(self):
        output = "CHECK OF model COMPLETED SUCCESSFULLY.\n"
        check_ok, _ = extract_om_success_flags(output)
        self.assertTrue(check_ok)

    def test_integrator_failed(self):
        output = (
            self._check_flag_output()
            + 'record SimulationResult\n  resultFile = "r.mat",\nintegrator failed\nend SimulationResult;\n'
        )
        _, sim_ok = extract_om_success_flags(output)
        self.assertFalse(sim_ok)


# ---------------------------------------------------------------------------
# classify_failure
# ---------------------------------------------------------------------------


class TestClassifyFailure(unittest.TestCase):
    def test_returns_strings(self):
        error_type, reason = classify_failure("some omc output", False, False)
        self.assertIsInstance(error_type, str)
        self.assertIsInstance(reason, str)

    def test_both_pass_gives_none_error(self):
        error_type, _ = classify_failure("Check of X completed successfully.", True, True)
        # When both pass there is no error
        self.assertEqual(error_type, "none")

    def test_check_fail_produces_error_type(self):
        error_type, reason = classify_failure("Error: underconstrained", False, False)
        self.assertNotEqual(error_type, "")
        self.assertIsInstance(reason, str)


class TestRunCheckAndSimulateScript(unittest.TestCase):
    def test_includes_requested_solver_method(self):
        output = (
            "Check of ModelA completed successfully.\n"
            'record SimulationResult\n  resultFile = "ModelA_res.mat",\n'
            '  messages = "LOG_SUCCESS | The simulation finished successfully.",\n'
            "end SimulationResult;\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "gateforge.agent_modelica_omc_workspace_v1.run_omc_script_local",
                return_value=(0, output),
            ) as runner:
                run_check_and_simulate(
                    workspace=Path(tmp),
                    model_load_files=["ModelA.mo"],
                    model_name="ModelA",
                    timeout_sec=20,
                    backend="omc",
                    docker_image="unused",
                    stop_time=1.0,
                    intervals=100,
                    method="ida",
                )
        script = runner.call_args.args[0]
        self.assertIn('method="ida"', script)


# ---------------------------------------------------------------------------
# prepare_workspace_model_layout (fallback path — no real FS copy needed)
# ---------------------------------------------------------------------------


class TestPrepareWorkspaceModelLayout(unittest.TestCase):
    def test_fallback_path_no_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            layout = prepare_workspace_model_layout(
                workspace=ws,
                fallback_model_path=Path("/some/Mutated.mo"),
                primary_model_name="Mutated",
            )
            self.assertIsInstance(layout, WorkspaceModelLayout)
            self.assertEqual(layout.model_write_path, ws / "Mutated.mo")
            self.assertEqual(layout.model_identifier, "Mutated")
            self.assertFalse(layout.uses_external_library)
            self.assertEqual(len(layout.model_load_files), 1)
            self.assertEqual(layout.model_load_files[0], "Mutated.mo")

    def test_fallback_path_incomplete_library_args(self):
        # Only package_name provided (not all three), falls through to fallback
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            layout = prepare_workspace_model_layout(
                workspace=ws,
                fallback_model_path=Path("/x/Model.mo"),
                primary_model_name="Model",
                source_package_name="Foo",  # missing source_library_path
            )
            self.assertFalse(layout.uses_external_library)
            self.assertEqual(layout.model_identifier, "Model")

    def test_model_load_files_uses_forward_slash(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            layout = prepare_workspace_model_layout(
                workspace=ws,
                fallback_model_path=Path("/x/MyModel.mo"),
                primary_model_name="MyModel",
            )
            for f in layout.model_load_files:
                self.assertNotIn("\\", f)


# ---------------------------------------------------------------------------
# copytree_best_effort
# ---------------------------------------------------------------------------


class TestCopytreeBestEffort(unittest.TestCase):
    def test_copies_directory(self):
        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as dst_tmp:
            src = Path(src_tmp) / "src"
            src.mkdir()
            (src / "file.mo").write_text("model X end X;")
            dst = Path(dst_tmp) / "dst"
            result = copytree_best_effort(src, dst)
            self.assertTrue(result)
            self.assertTrue((dst / "file.mo").exists())

    def test_returns_false_on_error(self):
        result = copytree_best_effort(Path("/nonexistent/src"), Path("/nonexistent/dst"))
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# cleanup_workspace_best_effort
# ---------------------------------------------------------------------------


class TestCleanupWorkspaceBestEffort(unittest.TestCase):
    def test_removes_directory(self):
        td = tempfile.mkdtemp()
        Path(td, "file.txt").write_text("hello")
        cleanup_workspace_best_effort(td)
        self.assertFalse(Path(td).exists())

    def test_nonexistent_path_no_error(self):
        cleanup_workspace_best_effort("/nonexistent/path/xyz")  # should not raise


# ---------------------------------------------------------------------------
# temporary_workspace
# ---------------------------------------------------------------------------


class TestTemporaryWorkspace(unittest.TestCase):
    def test_creates_and_cleans_up(self):
        captured = []
        with temporary_workspace(prefix="gf_test_") as td:
            captured.append(td)
            self.assertTrue(Path(td).is_dir())
        self.assertFalse(Path(captured[0]).exists())

    def test_prefix_applied(self):
        with temporary_workspace(prefix="gf_myprefix_") as td:
            self.assertIn("gf_myprefix_", td)

    def test_cleanup_even_on_exception(self):
        captured = []
        try:
            with temporary_workspace(prefix="gf_exc_") as td:
                captured.append(td)
                raise ValueError("deliberate")
        except ValueError:
            pass
        self.assertFalse(Path(captured[0]).exists())

    def test_defaults_to_repo_tmp_docker_root(self):
        root = _workspace_tmp_root()
        self.assertIsNotNone(root)
        self.assertEqual(root, Path(__file__).resolve().parents[1] / "tmp" / "docker")
        with temporary_workspace(prefix="gf_repo_tmp_") as td:
            self.assertTrue(str(td).startswith(str(root)))

    def test_env_override_changes_workspace_root(self):
        with tempfile.TemporaryDirectory(prefix="gf_custom_tmp_root_") as td:
            prev = os.environ.get("GATEFORGE_AGENT_TMP_ROOT")
            os.environ["GATEFORGE_AGENT_TMP_ROOT"] = td
            try:
                root = _workspace_tmp_root()
                self.assertEqual(root, Path(td))
                with temporary_workspace(prefix="gf_env_tmp_") as workspace:
                    self.assertTrue(str(workspace).startswith(td))
            finally:
                if prev is None:
                    os.environ.pop("GATEFORGE_AGENT_TMP_ROOT", None)
                else:
                    os.environ["GATEFORGE_AGENT_TMP_ROOT"] = prev


if __name__ == "__main__":
    unittest.main()
