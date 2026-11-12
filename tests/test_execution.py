"""Unit tests for tools/sbp_gen/execution.py's ExecutionConfig.

Covers from_payload()'s validation branches (cli/api/transport, including the
new gemini provider), and child_environment()'s credential handling -- the
one place that actually decides which API key env var a worker subprocess
sees.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from sbp_gen import execution  # noqa: E402


class FromPayloadCliModeTests(unittest.TestCase):
    def test_missing_mode_raises(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({})

    def test_unknown_mode_raises(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "not-a-mode"})

    def test_cli_with_api_key_is_rejected(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "cli", "executor": "codex", "api_key": "sk-x"})

    def test_cli_missing_executor_raises(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "cli"})

    def test_cli_unknown_executor_raises(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "cli", "executor": "not-a-real-executor"})

    def test_cli_invalid_effort_for_executor_raises(self):
        # "max" is a valid Claude Code effort but not a valid Codex one.
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "cli", "executor": "codex", "codex_effort": "max"})

    def test_cli_valid_minimal(self):
        config = execution.ExecutionConfig.from_payload({"mode": "cli", "executor": "claude"})
        self.assertEqual(config.mode, "cli")
        self.assertEqual(config.executor, "claude")
        self.assertIsNone(config.api_key)

    def test_cli_valid_with_model_and_effort(self):
        config = execution.ExecutionConfig.from_payload(
            {"mode": "cli", "executor": "claude", "codex_model": "opus", "codex_effort": "max"})
        self.assertEqual(config.codex_model, "opus")
        self.assertEqual(config.codex_effort, "max")


class FromPayloadApiModeTests(unittest.TestCase):
    def test_unsupported_provider_raises(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "api", "provider": "not-a-real-provider"})

    def test_api_without_key_raises(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "api", "provider": "openai"})

    def test_api_with_blank_key_raises(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "api", "provider": "openai", "api_key": "   "})

    def test_openai_valid(self):
        config = execution.ExecutionConfig.from_payload({"mode": "api", "provider": "openai", "api_key": "sk-x"})
        self.assertEqual(config.provider, "openai")
        self.assertEqual(config.api_key, "sk-x")

    def test_gemini_valid(self):
        config = execution.ExecutionConfig.from_payload({"mode": "api", "provider": "gemini", "api_key": "AIza-x"})
        self.assertEqual(config.mode, "api")
        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.api_key, "AIza-x")

    def test_api_key_with_surrounding_whitespace_is_trimmed(self):
        # A key pasted with a leading/trailing newline or space must not reach
        # the child environment verbatim -- it becomes an HTTP header value,
        # and some whitespace-ish characters there raise deep inside the SDK
        # instead of here, at validation time.
        config = execution.ExecutionConfig.from_payload(
            {"mode": "api", "provider": "gemini", "api_key": "  AIza-x\n"}
        )
        self.assertEqual(config.api_key, "AIza-x")

    def test_api_key_with_invisible_non_ascii_character_raises_clearly(self):
        # Reproduces the real-world failure: a zero-width space copied along
        # with the key survives .strip() (it's not whitespace) and previously
        # reached the Gemini SDK's HTTP layer, which raised a cryptic
        # UnicodeEncodeError. This must now be caught at validation time with
        # an actionable message instead.
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload(
                {"mode": "api", "provider": "gemini", "api_key": "AIza​-x"}
            )


class FromPayloadTransportModeTests(unittest.TestCase):
    def test_transport_rejected_by_default(self):
        with self.assertRaises(execution.ExecutionConfigError):
            execution.ExecutionConfig.from_payload({"mode": "transport"})

    def test_transport_allowed_when_flagged(self):
        config = execution.ExecutionConfig.from_payload({"mode": "transport"}, allow_transport=True)
        self.assertEqual(config.mode, "transport")


class ChildEnvironmentTests(unittest.TestCase):
    def test_gemini_api_mode_sets_gemini_key_only(self):
        config = execution.ExecutionConfig(mode="api", provider="gemini", api_key="AIza-x")
        env = config.child_environment(base={})
        self.assertEqual(env["GEMINI_API_KEY"], "AIza-x")
        self.assertEqual(env["SBP_LLM_PROVIDER"], "gemini")
        self.assertEqual(env["SBP_API_PROVIDER"], "gemini")
        self.assertNotIn("OPENAI_API_KEY", env)

    def test_openai_api_mode_sets_openai_key_only(self):
        config = execution.ExecutionConfig(mode="api", provider="openai", api_key="sk-x")
        env = config.child_environment(base={})
        self.assertEqual(env["OPENAI_API_KEY"], "sk-x")
        self.assertNotIn("GEMINI_API_KEY", env)

    def test_strips_ambient_credentials_not_belonging_to_this_request(self):
        """A key for some other provider sitting in the base environment must never
        leak into a worker that didn't ask for it."""
        base = {"GEMINI_API_KEY": "someone-elses-leftover-key", "PATH": "/usr/bin"}
        config = execution.ExecutionConfig(mode="api", provider="openai", api_key="sk-x")
        env = config.child_environment(base=base)
        self.assertNotIn("GEMINI_API_KEY", env)
        self.assertEqual(env["OPENAI_API_KEY"], "sk-x")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_cli_mode_sets_executor_and_clears_api_provider(self):
        base = {"SBP_API_PROVIDER": "stale-value"}
        config = execution.ExecutionConfig(mode="cli", executor="claude", codex_model="opus", codex_effort="max")
        env = config.child_environment(base=base)
        self.assertEqual(env["SBP_CLI_EXECUTOR"], "claude")
        self.assertEqual(env["SBP_CLI_MODEL"], "opus")
        self.assertEqual(env["SBP_CLI_EFFORT"], "max")
        self.assertNotIn("SBP_API_PROVIDER", env)


class FromEnvironmentTests(unittest.TestCase):
    def test_gemini_from_environment(self):
        import os
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {
            "SBP_EXECUTION_MODE": "api", "SBP_API_PROVIDER": "gemini", "GEMINI_API_KEY": "AIza-env",
        }, clear=False):
            config = execution.ExecutionConfig.from_environment()
        self.assertEqual(config.provider, "gemini")
        self.assertEqual(config.api_key, "AIza-env")

    def test_api_mode_missing_key_raises(self):
        import os
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {
            "SBP_EXECUTION_MODE": "api", "SBP_API_PROVIDER": "gemini",
        }, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            with self.assertRaises(execution.ExecutionConfigError):
                execution.ExecutionConfig.from_environment()


if __name__ == "__main__":
    unittest.main()
