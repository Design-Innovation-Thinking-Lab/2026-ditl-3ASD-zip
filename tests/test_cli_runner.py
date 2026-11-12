"""Unit tests for tools/sbp_gen/cli_runner.py.

Covers clean_diagnostic() -- the diagnostic-masking function that is this
codebase's main defence against a CLI failure message leaking participant
transcript text, secrets, emails or home-directory paths -- plus the
stage-aware timeout resolution (stage_timeout/effective_timeout), which has
real incident history (see the module's own comments) but no prior tests.
"""
from __future__ import annotations

import os
import pathlib
import sys
import unittest
import unittest.mock as mock

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from sbp_gen import cli_runner  # noqa: E402


class CleanDiagnosticTests(unittest.TestCase):
    def test_non_string_input_returns_none(self):
        self.assertIsNone(cli_runner.clean_diagnostic(None))
        self.assertIsNone(cli_runner.clean_diagnostic(42))

    def test_empty_or_whitespace_returns_none(self):
        self.assertIsNone(cli_runner.clean_diagnostic(""))
        self.assertIsNone(cli_runner.clean_diagnostic("   \n  "))

    def test_masks_sk_style_api_key(self):
        result = cli_runner.clean_diagnostic("failed with key sk-abcdefgh12345678")
        self.assertNotIn("sk-abcdefgh12345678", result)
        self.assertIn("[redacted]", result)

    def test_masks_bearer_token(self):
        result = cli_runner.clean_diagnostic("sent header Bearer abc.def.ghi123")
        self.assertNotIn("abc.def.ghi123", result)
        self.assertIn("[redacted]", result)

    def test_masks_jwt_style_token(self):
        result = cli_runner.clean_diagnostic("token was eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0")
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", result)

    def test_masks_key_value_style_secret(self):
        result = cli_runner.clean_diagnostic("config: api_key=verysecretvalue123")
        self.assertNotIn("verysecretvalue123", result)
        self.assertIn("[redacted]", result)

    def test_masks_email_address(self):
        result = cli_runner.clean_diagnostic("logged in as researcher@example.com today")
        self.assertNotIn("researcher@example.com", result)
        self.assertIn("[redacted]", result)

    def test_masks_home_directory_path(self):
        result = cli_runner.clean_diagnostic("could not write to /Users/lucas/secret-project/data")
        self.assertNotIn("/Users/lucas", result)
        self.assertIn("~", result)

    def test_masks_overlap_with_prompt_text(self):
        """A 12+ character stretch shared with the prompt must be masked, so a
        participant's own transcript text cannot ride out on an error message."""
        prompt = "The participant said: I have been struggling with onboarding for months."
        failure_text = "runner crashed while processing: I have been struggling with onboarding"
        result = cli_runner.clean_diagnostic(failure_text, prompt)
        self.assertNotIn("I have been struggling with onboarding", result)

    def test_short_overlap_below_window_is_not_masked(self):
        """Fewer than 12 shared characters should not trigger masking -- that
        would make ordinary short words disappear from every message."""
        prompt = "research purpose: understand onboarding"
        failure_text = "the request failed"
        result = cli_runner.clean_diagnostic(failure_text, prompt)
        self.assertEqual(result, "the request failed")

    def test_result_that_is_only_the_mask_marker_returns_none(self):
        prompt = "exactly this twelve+ char secret phrase here"
        failure_text = "exactly this twelve+ char secret phrase here"
        self.assertIsNone(cli_runner.clean_diagnostic(failure_text, prompt))

    def test_truncates_long_text_to_limit(self):
        long_text = "x" * 500
        result = cli_runner.clean_diagnostic(long_text)
        self.assertLessEqual(len(result), cli_runner.DIAGNOSTIC_LIMIT)
        self.assertTrue(result.endswith("…"))

    def test_collapses_multiple_lines_and_whitespace(self):
        result = cli_runner.clean_diagnostic("line one\n\n  line   two  \nline three")
        self.assertEqual(result, "line one / line two / line three")


class StageTimeoutTests(unittest.TestCase):
    def setUp(self):
        # Make sure no stray env override or context-var deadline leaks between tests.
        for name in list(os.environ):
            if name.startswith("SBP_CLI_RUNNER_TIMEOUT"):
                os.environ.pop(name)

    def test_default_is_600_for_an_unlisted_stage(self):
        self.assertEqual(cli_runner.stage_timeout("some_small_stage"), 600.0)

    def test_stage_with_a_known_floor_uses_the_floor(self):
        # "align" has a documented 1200s floor from real timeout incidents.
        self.assertEqual(cli_runner.stage_timeout("align"), 1200.0)

    def test_global_override_raises_every_stage(self):
        with mock.patch.dict(os.environ, {"SBP_CLI_RUNNER_TIMEOUT": "2000"}):
            self.assertEqual(cli_runner.stage_timeout("some_small_stage"), 2000.0)
            # A global override can lengthen a stage past its floor...
            self.assertEqual(cli_runner.stage_timeout("align"), 2000.0)

    def test_global_override_cannot_cut_below_a_stage_floor(self):
        with mock.patch.dict(os.environ, {"SBP_CLI_RUNNER_TIMEOUT": "100"}):
            # ...but never below align's own 1200s floor.
            self.assertEqual(cli_runner.stage_timeout("align"), 1200.0)

    def test_stage_specific_override_wins_outright(self):
        with mock.patch.dict(os.environ, {"SBP_CLI_RUNNER_TIMEOUT_ALIGN": "50"}):
            self.assertEqual(cli_runner.stage_timeout("align"), 50.0)

    def test_explicit_call_timeout_overrides_everything(self):
        with cli_runner.call_timeout(12.0):
            self.assertEqual(cli_runner.stage_timeout("align"), 12.0)
        # And it must not leak past the context manager's exit.
        self.assertEqual(cli_runner.stage_timeout("align"), 1200.0)


class EffectiveTimeoutTests(unittest.TestCase):
    def test_no_deadline_returns_requested_value(self):
        self.assertEqual(cli_runner.effective_timeout(42.0), 42.0)

    def test_deadline_clips_a_longer_request(self):
        with cli_runner.deadline(1.0):
            clipped = cli_runner.effective_timeout(600.0)
            self.assertLessEqual(clipped, 1.0)

    def test_expired_deadline_raises(self):
        with cli_runner.deadline(0.0):
            with self.assertRaises(cli_runner.CLIRunnerDeadlineExceeded):
                cli_runner.effective_timeout(5.0)

    def test_nested_deadline_never_extends_the_outer_one(self):
        with cli_runner.deadline(1.0):
            with cli_runner.deadline(600.0):
                clipped = cli_runner.effective_timeout(600.0)
                self.assertLessEqual(clipped, 1.0)


if __name__ == "__main__":
    unittest.main()
