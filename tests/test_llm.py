"""Unit tests for tools/sbp_gen/llm.py's provider-resolution logic.

resolve_provider()/default_model()/effort_for_record() decide, per pipeline
stage, which backend actually runs and what gets recorded as having run --
get this wrong and a run's provenance ("which model produced this claim")
becomes untrustworthy, which is exactly the property this project's
evidence-traceability design depends on.
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

from sbp_gen import llm  # noqa: E402

_ENV_KEYS_TO_CLEAR = ("SBP_LLM_PROVIDER", "SBP_ALIGN_PROVIDER")


class ResolveProviderTests(unittest.TestCase):
    def setUp(self):
        self._patcher = mock.patch.dict(os.environ, {}, clear=False)
        self._patcher.start()
        for key in _ENV_KEYS_TO_CLEAR:
            os.environ.pop(key, None)

    def tearDown(self):
        self._patcher.stop()

    def test_explicit_argument_wins_over_everything(self):
        os.environ["SBP_LLM_PROVIDER"] = "gemini"
        self.assertEqual(llm.resolve_provider("openai", stage="align"), "openai")

    def test_defaults_to_anthropic_with_nothing_set(self):
        self.assertEqual(llm.resolve_provider(None, stage="align"), "anthropic")

    def test_stage_default_provider_applies_without_env_override(self):
        # journey_recheck defaults to gemini per STAGE_DEFAULT_PROVIDERS.
        self.assertEqual(llm.resolve_provider(None, stage="journey_recheck"), "gemini")

    def test_global_env_override_wins_over_stage_default(self):
        os.environ["SBP_LLM_PROVIDER"] = "openai"
        self.assertEqual(llm.resolve_provider(None, stage="journey_recheck"), "openai")

    def test_stage_specific_env_override_wins_over_global(self):
        os.environ["SBP_LLM_PROVIDER"] = "openai"
        os.environ["SBP_ALIGN_PROVIDER"] = "gemini"
        self.assertEqual(llm.resolve_provider(None, stage="align"), "gemini")

    def test_unsupported_provider_raises(self):
        with self.assertRaises(llm.LLMError):
            llm.resolve_provider("not-a-real-provider", stage="align")

    def test_provider_is_normalized_to_lowercase(self):
        self.assertEqual(llm.resolve_provider("OpenAI", stage="align"), "openai")


class DefaultModelTests(unittest.TestCase):
    def test_anthropic_default(self):
        self.assertEqual(llm.default_model("align", "anthropic"), "claude-sonnet-5")

    def test_gemini_default_comes_from_gemini_llm(self):
        from sbp_gen.gemini_llm import DEFAULT_MODELS as GEMINI_MODELS
        self.assertEqual(llm.default_model("align", "gemini"), GEMINI_MODELS["align"])

    def test_openai_default_comes_from_openai_llm(self):
        from sbp_gen.openai_llm import DEFAULT_MODELS as OPENAI_MODELS
        self.assertEqual(llm.default_model("align", "openai"), OPENAI_MODELS["align"])

    def test_cli_mode_reports_authenticated_cli_placeholder_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SBP_CLI_MODEL", None)
            self.assertEqual(llm.default_model("align", "cli"), "authenticated-cli")

    def test_cli_mode_honours_an_explicit_model_override(self):
        with mock.patch.dict(os.environ, {"SBP_CLI_MODEL": "opus"}):
            self.assertEqual(llm.default_model("align", "cli"), "opus")


class EffortForRecordTests(unittest.TestCase):
    def test_anthropic_records_the_requested_effort(self):
        self.assertEqual(llm.effort_for_record("structure", "anthropic", "high"), "high")

    def test_anthropic_falls_back_to_stage_default_when_unspecified(self):
        self.assertEqual(llm.effort_for_record("structure", "anthropic", None), llm.DEFAULT_EFFORT["structure"])

    def test_non_anthropic_provider_never_claims_a_requested_effort(self):
        """A non-Anthropic call() silently discards `effort` -- recording the
        requested value anyway would misrepresent what the provider actually
        did. This is exactly the bug the module's own comment warns about."""
        self.assertEqual(llm.effort_for_record("structure", "openai", "high"), llm.NOT_SUPPORTED_BY_PROVIDER)
        self.assertEqual(llm.effort_for_record("structure", "gemini", "high"), llm.NOT_SUPPORTED_BY_PROVIDER)


class LiveProvidersTests(unittest.TestCase):
    def test_transport_is_excluded_from_live_providers(self):
        self.assertNotIn("transport", llm.LIVE_PROVIDERS)

    def test_every_other_provider_is_live(self):
        for provider in llm.PROVIDERS:
            if provider == "transport":
                continue
            self.assertIn(provider, llm.LIVE_PROVIDERS)

    def test_gemini_is_a_known_provider(self):
        self.assertIn("gemini", llm.PROVIDERS)


if __name__ == "__main__":
    unittest.main()
