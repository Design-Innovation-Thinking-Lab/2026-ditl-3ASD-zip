"""Unit tests for tools/sbp_review/review_execution.py.

The shared CLI-job execution/reporting helpers every domain's background
job depends on. Covers the pure parsing/formatting helpers directly, and
_axial_coding_run_dir's runs_dir/safe_component parameterization (the same
"explicit parameter, not a captured import" fix applied to review_upload.py's
uploads_dir, since server.py's RUNS_DIR is rebound at runtime).
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
import unittest.mock as mock

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from sbp_review import review_execution  # noqa: E402


class JobProgressTests(unittest.TestCase):
    def test_extracts_latest_progress_per_label(self):
        log = ["some text", "[進度] 開放編碼 3/16 類", "more text", "[進度] 開放編碼 5/16 類"]
        progress = review_execution._job_progress(log)
        self.assertEqual(progress["開放編碼"], (5, 16, "類"))

    def test_multiple_labels_tracked_independently(self):
        log = ["[進度] A 1/2 件", "[進度] B 3/4 份"]
        progress = review_execution._job_progress(log)
        self.assertEqual(progress["A"], (1, 2, "件"))
        self.assertEqual(progress["B"], (3, 4, "份"))

    def test_non_list_input_returns_empty(self):
        self.assertEqual(review_execution._job_progress(None), {})
        self.assertEqual(review_execution._job_progress("not a list"), {})

    def test_no_progress_lines_returns_empty(self):
        self.assertEqual(review_execution._job_progress(["plain line", "another"]), {})


class WithProgressTests(unittest.TestCase):
    def test_appends_progress_when_label_present(self):
        result = review_execution._with_progress("正在整理開放編碼", "開放編碼", {"開放編碼": (6, 15, "份")})
        self.assertEqual(result, "正在整理開放編碼（6／15 份）")

    def test_returns_text_unchanged_when_label_absent(self):
        result = review_execution._with_progress("正在整理", "其他", {})
        self.assertEqual(result, "正在整理")


class RequireExecutionTests(unittest.TestCase):
    def test_passes_through_a_real_execution_config(self):
        from sbp_gen import execution as sbp_execution
        config = sbp_execution.ExecutionConfig(mode="cli", executor="claude")
        self.assertIs(review_execution._require_execution(config), config)

    def test_rejects_non_execution_config_values(self):
        from sbp_gen import execution as sbp_execution
        with self.assertRaises(sbp_execution.ExecutionConfigError):
            review_execution._require_execution(None)
        with self.assertRaises(sbp_execution.ExecutionConfigError):
            review_execution._require_execution({"mode": "cli"})


class RedactExecutionTextTests(unittest.TestCase):
    def test_no_config_returns_text_unchanged(self):
        self.assertEqual(review_execution._redact_execution_text("hello world"), "hello world")

    def test_redacts_the_api_key_when_present(self):
        from sbp_gen import execution as sbp_execution
        config = sbp_execution.ExecutionConfig(mode="api", provider="openai", api_key="sk-secret123")
        result = review_execution._redact_execution_text("error with key sk-secret123 in it", config)
        self.assertNotIn("sk-secret123", result)
        self.assertIn("[redacted]", result)

    def test_cli_mode_config_with_no_key_leaves_text_unchanged(self):
        from sbp_gen import execution as sbp_execution
        config = sbp_execution.ExecutionConfig(mode="cli", executor="claude")
        self.assertEqual(review_execution._redact_execution_text("plain log line", config), "plain log line")


class ResearcherReadableLogTests(unittest.TestCase):
    def test_strips_shell_command_echo_lines(self):
        lines = ["$ python3 -m sbp_gen form-hmw --run foo", "正常的進度訊息"]
        result = review_execution._researcher_readable_log(lines)
        self.assertEqual(result, ["正常的進度訊息"])

    def test_strips_manual_transport_instructions(self):
        lines = ["[手動傳輸] align 第 1 輪：提示詞已存好", "請把它整份交給 Claude"]
        result = review_execution._researcher_readable_log(lines)
        self.assertEqual(result, [])

    def test_strips_progress_lines(self):
        lines = ["[進度] 開放編碼 3/16 類", "一般訊息"]
        result = review_execution._researcher_readable_log(lines)
        self.assertEqual(result, ["一般訊息"])

    def test_collapses_absolute_paths_to_bare_filenames(self):
        lines = [f"寫入 {review_execution.REPO}/runs/foo/evidence.yaml 完成"]
        result = review_execution._researcher_readable_log(lines)
        self.assertEqual(result, ["寫入 evidence.yaml 完成"])

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(review_execution._researcher_readable_log(None), [])
        self.assertEqual(review_execution._researcher_readable_log([]), [])


class AxialCodingRunDirTests(unittest.TestCase):
    def test_resolves_an_existing_run_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            (runs_dir / "my-run").mkdir()
            result = review_execution._axial_coding_run_dir(
                "my-run", runs_dir=runs_dir, safe_component=lambda v, fb: v or fb)
            self.assertEqual(result, runs_dir / "my-run")

    def test_missing_run_name_raises_review_run_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            with self.assertRaises(review_execution.ReviewRunNotFound):
                review_execution._axial_coding_run_dir(
                    "", runs_dir=runs_dir, safe_component=lambda v, fb: v or fb)

    def test_nonexistent_run_directory_raises_review_run_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            with self.assertRaises(review_execution.ReviewRunNotFound):
                review_execution._axial_coding_run_dir(
                    "does-not-exist", runs_dir=runs_dir, safe_component=lambda v, fb: v or fb)

    def test_uses_the_runs_dir_passed_in_not_a_stale_default(self):
        """The whole reason this takes runs_dir as a parameter: two calls
        with two different directories must each resolve against their own,
        proving there's no load-time-captured default in play."""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            root_a, root_b = pathlib.Path(tmp_a), pathlib.Path(tmp_b)
            (root_a / "run").mkdir()
            (root_b / "run").mkdir()
            result_a = review_execution._axial_coding_run_dir(
                "run", runs_dir=root_a, safe_component=lambda v, fb: v or fb)
            result_b = review_execution._axial_coding_run_dir(
                "run", runs_dir=root_b, safe_component=lambda v, fb: v or fb)
            self.assertEqual(result_a, root_a / "run")
            self.assertEqual(result_b, root_b / "run")


class StreamStageTests(unittest.TestCase):
    def test_uses_the_shared_job_and_lock_from_review_jobs(self):
        """Confirms _stream_stage mutates the SAME _job object review_jobs
        exposes, not a private copy -- the whole point of importing it
        directly instead of taking it as a parameter."""
        from sbp_gen import execution as sbp_execution
        config = sbp_execution.ExecutionConfig(mode="cli", executor="claude")

        class FakeProc:
            stdout = iter(["line one", "line two"])
            def wait(self):
                return 0

        with mock.patch("sbp_review.review_execution.child_processes.spawn", return_value=FakeProc()):
            review_execution._stream_stage(["python3", "-m", "sbp_gen", "x"], "測試階段", config)
        from sbp_review.review_jobs import _job
        self.assertIn("line one", _job["log"])
        self.assertIn("line two", _job["log"])


if __name__ == "__main__":
    unittest.main()
