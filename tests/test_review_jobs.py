"""Unit tests for tools/sbp_review/review_jobs.py.

This module holds the single process-wide "what job is running right now"
state, extracted out of server.py so future domain modules (journey,
persona, ...) have a neutral place to import it from instead of reaching
into server.py itself. Covers the dependency-injection wiring (configure()),
_JobState's diagnostic-on-finish behavior, and JOB_KIND_ACTIVE_PHRASES'
completeness -- the extraction itself required manually re-typing this
dict, and two entries were missed on the first pass, caught only by
re-reading the original file line by line. A completeness test guards
against that class of mistake happening silently again.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
import unittest.mock as mock

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from sbp_review import review_jobs  # noqa: E402


class ConfigureTests(unittest.TestCase):
    def tearDown(self):
        # These tests mutate module-level state; restore it so other test
        # files (and other tests in this file) don't see a stale resolver.
        review_jobs.configure(
            data_root_resolver=lambda: pathlib.Path("/unused"),
            failure_cause_resolver=lambda log: {"cause": "unused"},
        )

    def test_configure_wires_in_both_resolvers(self):
        root_fn = lambda: pathlib.Path("/data")  # noqa: E731
        cause_fn = lambda log: {"cause": "x"}  # noqa: E731
        review_jobs.configure(data_root_resolver=root_fn, failure_cause_resolver=cause_fn)
        self.assertIs(review_jobs._data_root_resolver, root_fn)
        self.assertIs(review_jobs._failure_cause_resolver, cause_fn)


class JobStateFinishedSinceTests(unittest.TestCase):
    def setUp(self):
        self.recorded = []
        review_jobs.configure(
            data_root_resolver=lambda: pathlib.Path("/fake/data/root"),
            failure_cause_resolver=lambda log: {"cause": "whatever"},
        )

    def tearDown(self):
        review_jobs.configure(
            data_root_resolver=lambda: pathlib.Path("/unused"),
            failure_cause_resolver=lambda log: {"cause": "unused"},
        )

    def test_transition_from_running_to_done_records_a_diagnostic(self):
        job = review_jobs._JobState({"kind": "fresh_coding", "state": "running", "started_monotonic": 1.0})
        with mock.patch("sbp_review.review_jobs.sbp_diagnostics.record") as record:
            job["state"] = "done"
        record.assert_called_once()

    def test_transition_without_started_monotonic_records_nothing(self):
        """A job that never went through the real start path has no
        start time -- must not crash or fabricate a diagnostic for it."""
        job = review_jobs._JobState({"kind": "fresh_coding", "state": "running"})
        with mock.patch("sbp_review.review_jobs.sbp_diagnostics.record") as record:
            job["state"] = "done"
        record.assert_not_called()

    def test_transition_between_two_active_states_records_nothing(self):
        job = review_jobs._JobState({"kind": "fresh_coding", "state": "running", "started_monotonic": 1.0})
        with mock.patch("sbp_review.review_jobs.sbp_diagnostics.record") as record:
            job["state"] = "waiting_for_model"
        record.assert_not_called()

    def test_transition_from_idle_to_done_records_nothing(self):
        """idle is neither an active nor finished state transition source --
        only running/waiting_for_model -> done/failed/cancelled counts."""
        job = review_jobs._JobState({"kind": None, "state": "idle", "started_monotonic": 1.0})
        with mock.patch("sbp_review.review_jobs.sbp_diagnostics.record") as record:
            job["state"] = "done"
        record.assert_not_called()

    def test_setting_an_unrelated_key_never_triggers_a_diagnostic(self):
        job = review_jobs._JobState({"kind": "fresh_coding", "state": "running", "started_monotonic": 1.0})
        with mock.patch("sbp_review.review_jobs.sbp_diagnostics.record") as record:
            job["phase"] = "分析中"
        record.assert_not_called()

    def test_update_method_also_triggers_the_same_check(self):
        """update() is the other way callers mutate the dict (vs. a single
        __setitem__) -- it must go through the same finished-transition
        check, not bypass it."""
        job = review_jobs._JobState({"kind": "fresh_coding", "state": "running", "started_monotonic": 1.0})
        with mock.patch("sbp_review.review_jobs.sbp_diagnostics.record") as record:
            job.update({"state": "failed", "phase": None})
        record.assert_called_once()

    def test_unconfigured_resolvers_skip_recording_without_raising(self):
        review_jobs._data_root_resolver = None
        review_jobs._failure_cause_resolver = None
        job = review_jobs._JobState({"kind": "fresh_coding", "state": "running", "started_monotonic": 1.0})
        with mock.patch("sbp_review.review_jobs.sbp_diagnostics.record") as record:
            job["state"] = "done"  # must not raise
        record.assert_not_called()

    def test_diagnostics_failure_is_swallowed_not_propagated(self):
        """diagnostics must never change a job's actual outcome -- a broken
        diagnostics backend should not make the state assignment itself
        raise."""
        job = review_jobs._JobState({"kind": "fresh_coding", "state": "running", "started_monotonic": 1.0})
        with mock.patch("sbp_review.review_jobs.sbp_diagnostics.record", side_effect=RuntimeError("disk full")):
            job["state"] = "done"  # must not raise


class JobKindActivePhraseTests(unittest.TestCase):
    def test_every_operation_kind_constant_has_a_phrase(self):
        """Guards against exactly the mistake made while writing this
        extraction: two entries were silently missed on the first copy."""
        operation_kinds = [
            review_jobs.AS_IS_GENERATION_OPERATION_KIND,
            review_jobs.FRESH_CODING_OPERATION_KIND,
            review_jobs.PERSONA_CONTENT_GENERATION_OPERATION_KIND,
            review_jobs.HMW_FORMATION_OPERATION_KIND,
            review_jobs.JOURNEY_GENERATION_OPERATION_KIND,
            review_jobs.JOURNEY_SOLUTIONS_OPERATION_KIND,
            review_jobs.AS_IS_BLUEPRINT_OPERATION_KIND,
            review_jobs.CHART_GUIDE_OPERATION_KIND,
            review_jobs.SELECTIVE_THEMES_OPERATION_KIND,
            review_jobs.JTBD_CANDIDATES_OPERATION_KIND,
            review_jobs.IMPLEMENTATION_RESEARCH_OPERATION_KIND,
        ]
        for kind in operation_kinds:
            self.assertIn(kind, review_jobs.JOB_KIND_ACTIVE_PHRASES, f"missing phrase for {kind!r}")

    def test_string_literal_kinds_not_backed_by_a_named_constant(self):
        """journey_recheck/to_be_generation/as_is_research are real kinds
        used elsewhere as bare string literals, not exposed as named
        constants -- confirm they're still present after the copy."""
        for kind in ("journey_recheck", "to_be_generation", "as_is_research"):
            self.assertIn(kind, review_jobs.JOB_KIND_ACTIVE_PHRASES)

    def test_unknown_kind_falls_back_to_as_is_generation_phrase(self):
        fallback = review_jobs.JOB_KIND_ACTIVE_PHRASES[review_jobs.AS_IS_GENERATION_OPERATION_KIND]
        self.assertEqual(review_jobs._job_kind_active_phrase("not-a-real-kind"), fallback)
        self.assertEqual(review_jobs._job_kind_active_phrase(None), fallback)

    def test_known_kind_returns_its_own_phrase_not_the_fallback(self):
        phrase = review_jobs._job_kind_active_phrase(review_jobs.HMW_FORMATION_OPERATION_KIND)
        self.assertEqual(phrase, "HMW 形成")


class SharedJobSingletonTests(unittest.TestCase):
    def test_job_and_job_lock_are_module_level_singletons(self):
        """Every domain that imports these must get the SAME object -- the
        whole point of this module existing is one shared whiteboard, not
        a fresh one per importer."""
        from sbp_review import review_jobs as review_jobs_again
        self.assertIs(review_jobs._job, review_jobs_again._job)
        self.assertIs(review_jobs._job_lock, review_jobs_again._job_lock)


if __name__ == "__main__":
    unittest.main()
