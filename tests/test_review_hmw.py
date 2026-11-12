"""Unit tests for tools/sbp_review/review_hmw.py.

HMW formation's job-status projection, cancellation, and the CLI-running
function, extracted out of server.py. _hmw_formation_job_view/_mark_hmw_
formation_cancelled take runs_dir/safe_component explicitly, same reasoning
as review_execution's _axial_coding_run_dir (server.py's RUNS_DIR is
rebound at runtime).
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

from sbp_review import review_hmw  # noqa: E402

_SAFE = lambda v, fb: v or fb  # noqa: E731


class HmwFormationJobViewTests(unittest.TestCase):
    def test_missing_run_returns_job_unchanged(self):
        """job["out"] pointing at a run that doesn't exist on disk must not
        crash the status poll -- it just gets the job back as-is."""
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            job = {"state": "running", "out": "no-such-run", "log": []}
            result = review_hmw._hmw_formation_job_view(job, runs_dir=runs_dir, safe_component=_SAFE)
            self.assertIs(result, job)

    def test_running_job_gets_a_phase_from_active_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            (runs_dir / "r1").mkdir()
            job = {"state": "running", "out": "r1", "log": []}
            fake_formation = {"active_stage": "primary_hmw"}
            with mock.patch("sbp_review.review_hmw.sbp_hmw_formation.formation_state", return_value=fake_formation), \
                 mock.patch("sbp_review.review_hmw.sbp_hmw_formation.STAGE_LABELS", {"primary_hmw": "主要 HMW"}):
                result = review_hmw._hmw_formation_job_view(job, runs_dir=runs_dir, safe_component=_SAFE)
            self.assertIn("主要 HMW", result["phase"])

    def test_failed_job_reports_the_failed_stage_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            (runs_dir / "r1").mkdir()
            job = {"state": "failed", "out": "r1", "log": []}
            fake_formation = {"failed_stage": "primary_hmw", "failure": {"cause": "x"}}
            with mock.patch("sbp_review.review_hmw.sbp_hmw_formation.formation_state", return_value=fake_formation), \
                 mock.patch("sbp_review.review_hmw.sbp_hmw_formation.STAGE_LABELS", {"primary_hmw": "主要 HMW"}):
                result = review_hmw._hmw_formation_job_view(job, runs_dir=runs_dir, safe_component=_SAFE)
            self.assertEqual(result["phase"], "主要 HMW沒有完成")
            self.assertEqual(result["failure"], {"cause": "x"})

    def test_log_is_always_filtered_through_researcher_readable_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            (runs_dir / "r1").mkdir()
            job = {"state": "idle", "out": "r1", "log": ["$ python3 -m sbp_gen form-hmw", "真正的訊息"]}
            with mock.patch("sbp_review.review_hmw.sbp_hmw_formation.formation_state", return_value={}):
                result = review_hmw._hmw_formation_job_view(job, runs_dir=runs_dir, safe_component=_SAFE)
            self.assertEqual(result["log"], ["真正的訊息"])


class MarkHmwFormationCancelledTests(unittest.TestCase):
    def test_missing_run_is_a_silent_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            with mock.patch("sbp_review.review_hmw.sbp_hmw_formation.update_state") as update_state:
                review_hmw._mark_hmw_formation_cancelled("no-such-run", runs_dir=runs_dir, safe_component=_SAFE)
            update_state.assert_not_called()

    def test_existing_run_is_marked_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = pathlib.Path(tmp)
            (runs_dir / "r1").mkdir()
            with mock.patch("sbp_review.review_hmw.sbp_hmw_formation.update_state") as update_state:
                review_hmw._mark_hmw_formation_cancelled("r1", runs_dir=runs_dir, safe_component=_SAFE)
            update_state.assert_called_once_with(runs_dir / "r1", state="cancelled", active_stage=None)


class RunHmwFormationCliTests(unittest.TestCase):
    def setUp(self):
        from sbp_review.review_jobs import _job
        self._original = dict(_job)

    def tearDown(self):
        from sbp_review.review_jobs import _job
        _job.clear()
        _job.update(self._original)

    def test_success_marks_job_done(self):
        from sbp_gen import execution as sbp_execution
        from sbp_review.review_jobs import _job
        config = sbp_execution.ExecutionConfig(mode="cli", executor="claude")
        with mock.patch("sbp_review.review_hmw._stream_stage", return_value=0):
            review_hmw._run_hmw_formation_cli(pathlib.Path("/fake/run"), config)
        self.assertEqual(_job["state"], "done")
        self.assertEqual(_job["phase"], "HMW 候選已形成")

    def test_nonzero_exit_marks_job_failed(self):
        from sbp_gen import execution as sbp_execution
        from sbp_review.review_jobs import _job
        config = sbp_execution.ExecutionConfig(mode="cli", executor="claude")
        with mock.patch("sbp_review.review_hmw._stream_stage", return_value=1):
            review_hmw._run_hmw_formation_cli(pathlib.Path("/fake/run"), config)
        self.assertEqual(_job["state"], "failed")

    def test_transport_suspended_exit_marks_waiting_for_model(self):
        from sbp_gen import execution as sbp_execution
        from sbp_review.review_jobs import _job
        config = sbp_execution.ExecutionConfig(mode="transport")
        with mock.patch("sbp_review.review_hmw._stream_stage", return_value=review_hmw.TRANSPORT_SUSPENDED_EXIT):
            review_hmw._run_hmw_formation_cli(pathlib.Path("/fake/run"), config)
        self.assertEqual(_job["state"], "waiting_for_model")

    def test_oserror_from_stream_stage_marks_job_failed_with_reason(self):
        from sbp_gen import execution as sbp_execution
        from sbp_review.review_jobs import _job
        config = sbp_execution.ExecutionConfig(mode="cli", executor="claude")
        with mock.patch("sbp_review.review_hmw._stream_stage", side_effect=OSError("boom")):
            review_hmw._run_hmw_formation_cli(pathlib.Path("/fake/run"), config)
        self.assertEqual(_job["state"], "failed")
        self.assertIn("boom", _job["phase"])

    def test_already_cancelled_job_is_left_alone(self):
        """If the researcher cancelled while the subprocess was still
        running, a late 0/non-zero exit must not overwrite 'cancelled'."""
        from sbp_gen import execution as sbp_execution
        from sbp_review.review_jobs import _job
        config = sbp_execution.ExecutionConfig(mode="cli", executor="claude")
        _job["state"] = "cancelled"
        with mock.patch("sbp_review.review_hmw._stream_stage", return_value=0):
            review_hmw._run_hmw_formation_cli(pathlib.Path("/fake/run"), config)
        self.assertEqual(_job["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
