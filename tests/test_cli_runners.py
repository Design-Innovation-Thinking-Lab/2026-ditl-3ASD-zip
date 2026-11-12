"""Unit tests for tools/cli_runners/claude.py and codex.py.

Covers the keyword-based failure classifiers (classify_failure/_mentions --
pure functions, duplicated between the two wrappers, worth locking in before
any future dedup) and the credential-stripping invariant that is the whole
point of claude.py's "no --bare" design: runner_environment() must never let
an ambient API key reach the CLI subprocess, so it can't silently override
the researcher's own OAuth login state.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from cli_runners import claude as claude_runner  # noqa: E402
from cli_runners import codex as codex_runner  # noqa: E402


class ClaudeClassifyFailureTests(unittest.TestCase):
    def test_not_authenticated(self):
        err = claude_runner.classify_failure("Error: not logged in", "", 1, "")
        self.assertEqual(err.code, "runner_not_authenticated")

    def test_rate_limited(self):
        err = claude_runner.classify_failure("", "429 rate limit exceeded", 1, "")
        self.assertEqual(err.code, "runner_rate_limited")

    def test_model_unavailable(self):
        err = claude_runner.classify_failure("model not found: foo", "", 1, "")
        self.assertEqual(err.code, "runner_model_unavailable")

    def test_cli_usage_error(self):
        err = claude_runner.classify_failure("", "unknown option --foo\nusage: claude ...", 1, "")
        self.assertEqual(err.code, "runner_cli_usage_error")

    def test_network_unavailable(self):
        err = claude_runner.classify_failure("", "econnrefused", 1, "")
        self.assertEqual(err.code, "runner_network_unavailable")

    def test_unrecognized_failure_falls_back_to_generic(self):
        err = claude_runner.classify_failure("something totally unexpected happened", "", 1, "")
        self.assertEqual(err.code, "runner_failed")

    def test_diagnostic_is_masked(self):
        err = claude_runner.classify_failure("not logged in, key sk-abcdefgh12345678", "", 1, "")
        self.assertNotIn("sk-abcdefgh12345678", err.diagnostic or "")

    def test_digit_marker_does_not_match_inside_a_longer_number(self):
        """'429' must not match inside '14290' -- the digit-boundary check in
        _mentions exists specifically to avoid this kind of false positive."""
        self.assertFalse(claude_runner._mentions("processed 14290 tokens", "429"))
        self.assertTrue(claude_runner._mentions("got http 429 back", "429"))


class CodexClassifyFailureTests(unittest.TestCase):
    def test_returncode_2_is_always_usage_error(self):
        """Codex's own exit-code convention: 2 means CLI usage error regardless
        of what the (possibly empty) event stream says."""
        err = codex_runner.classify_failure("", "", 2, "")
        self.assertEqual(err.code, "runner_cli_usage_error")

    def test_not_authenticated(self):
        err = codex_runner.classify_failure("", "401 unauthorized", 1, "")
        self.assertEqual(err.code, "runner_not_authenticated")

    def test_rate_limited(self):
        err = codex_runner.classify_failure("", "429 too many requests", 1, "")
        self.assertEqual(err.code, "runner_rate_limited")

    def test_request_rejected(self):
        err = codex_runner.classify_failure("", "400 bad request: context window exceeded", 1, "")
        self.assertEqual(err.code, "runner_request_rejected")

    def test_reads_error_from_json_event_stream_over_stderr(self):
        events = '{"type": "error", "message": "model not found: gpt-nonexistent"}'
        err = codex_runner.classify_failure(events, "some unrelated stderr noise", 1, "")
        self.assertEqual(err.code, "runner_model_unavailable")

    def test_turn_failed_event_is_also_read(self):
        events = '{"type": "turn.failed", "error": {"message": "connection reset, dns failure"}}'
        err = codex_runner.classify_failure(events, "", 1, "")
        self.assertEqual(err.code, "runner_network_unavailable")

    def test_unrecognized_failure_falls_back_to_generic(self):
        err = codex_runner.classify_failure("", "nothing we recognize here", 1, "")
        self.assertEqual(err.code, "runner_failed")


class CodexUsageParsingTests(unittest.TestCase):
    def test_usage_tokens_sums_turn_completed_events(self):
        events = "\n".join([
            '{"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}}',
            '{"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 3}}',
            '{"type": "something.else", "usage": {"input_tokens": 999}}',
        ])
        self.assertEqual(codex_runner._usage_tokens(events), 128)

    def test_usage_tokens_ignores_malformed_lines(self):
        events = "not json\n" + '{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 0}}'
        self.assertEqual(codex_runner._usage_tokens(events), 10)

    def test_usage_detail_only_includes_fields_codex_actually_reported(self):
        events = '{"type": "turn.completed", "usage": {"input_tokens": 10, "reasoning_output_tokens": 4}}'
        detail = codex_runner._usage_detail(events)
        self.assertEqual(detail, {"input_tokens": 10, "reasoning_output_tokens": 4})
        self.assertNotIn("output_tokens", detail)


class ClaudeRunnerEnvironmentTests(unittest.TestCase):
    def test_strips_anthropic_api_key(self):
        """The whole point of not using --bare: an ambient ANTHROPIC_API_KEY must
        never reach the subprocess and silently override the OAuth login state."""
        base = {"ANTHROPIC_API_KEY": "sk-ant-leaked", "PATH": "/usr/bin"}
        env = claude_runner.runner_environment(base)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_build_command_never_includes_bare(self):
        command = claude_runner.build_command(
            "/usr/local/bin/claude", output_schema=None, model=None, effort=None)
        self.assertNotIn("--bare", command)

    def test_build_command_includes_model_and_effort_when_given(self):
        command = claude_runner.build_command(
            "/usr/local/bin/claude", output_schema=None, model="opus", effort="max")
        self.assertIn("--model", command)
        self.assertIn("opus", command)
        self.assertIn("--effort", command)
        self.assertIn("max", command)


class CodexRunnerEnvironmentTests(unittest.TestCase):
    def test_strips_codex_api_credentials(self):
        base = {"OPENAI_API_KEY": "sk-leaked", "CODEX_API_KEY": "also-leaked", "PATH": "/usr/bin"}
        env = codex_runner.runner_environment(base)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_API_KEY", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_build_command_uses_sandboxed_read_only_mode(self):
        command = codex_runner.build_command(
            "/usr/local/bin/codex", pathlib.Path("/tmp/work"), pathlib.Path("/tmp/last.txt"),
            schema_path=None, web_search=False, model=None)
        self.assertIn("read-only", command)
        self.assertIn('web_search="disabled"', command)

    def test_build_command_enables_web_search_when_requested(self):
        command = codex_runner.build_command(
            "/usr/local/bin/codex", pathlib.Path("/tmp/work"), pathlib.Path("/tmp/last.txt"),
            schema_path=None, web_search=True, model=None)
        self.assertIn('web_search="live"', command)


if __name__ == "__main__":
    unittest.main()
