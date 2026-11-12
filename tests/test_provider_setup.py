"""Unit tests for tools/sbp_review/provider_setup.py.

Covers the connection-check logic (run_openai_check/run_gemini_check and the
shared _run_rest_liveness_check skeleton they sit on), status_for's CLI-vs-API
branching, and RecentTests' keying/eviction. Uses a fake `opener` wherever a
real network call would otherwise happen -- these functions already accept
one as an injectable parameter, so no mocking library or real HTTP is needed.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
import urllib.error

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from sbp_review import provider_setup  # noqa: E402


class FakeResponse:
    """Minimal context-manager stand-in for the object urlopen() returns."""

    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def opener_returning(status: int):
    def opener(request, timeout):
        return FakeResponse(status)
    return opener


def opener_raising_http_error(code: int):
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, code, "error", {}, None)
    return opener


def opener_unreachable():
    def opener(request, timeout):
        raise urllib.error.URLError("no route to host")
    return opener


class RunOpenAICheckTests(unittest.TestCase):
    def test_ok_on_200(self):
        result = provider_setup.run_openai_check(
            "sk-test", base_url="https://api.openai.com/v1", opener=opener_returning(200))
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["provider"], "openai")
        self.assertIsNone(result["cause"])

    def test_key_rejected_on_401(self):
        result = provider_setup.run_openai_check(
            "sk-test", base_url="https://api.openai.com/v1", opener=opener_raising_http_error(401))
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["cause"]["cause"], "api_key_rejected")

    def test_key_rejected_on_403(self):
        result = provider_setup.run_openai_check(
            "sk-test", base_url="https://api.openai.com/v1", opener=opener_raising_http_error(403))
        self.assertEqual(result["cause"]["cause"], "api_key_rejected")

    def test_rate_limited_on_429(self):
        result = provider_setup.run_openai_check(
            "sk-test", base_url="https://api.openai.com/v1", opener=opener_raising_http_error(429))
        self.assertEqual(result["cause"]["cause"], "rate_limited")

    def test_unreachable_on_network_error(self):
        result = provider_setup.run_openai_check(
            "sk-test", base_url="https://api.openai.com/v1", opener=opener_unreachable())
        self.assertEqual(result["cause"]["cause"], "unreachable")

    def test_generic_failure_on_unexpected_status(self):
        result = provider_setup.run_openai_check(
            "sk-test", base_url="https://api.openai.com/v1", opener=opener_raising_http_error(500))
        self.assertEqual(result["cause"]["cause"], "api_check_failed")

    def test_sends_bearer_header_not_query_param(self):
        seen = {}

        def opener(request, timeout):
            seen["headers"] = dict(request.headers)
            seen["url"] = request.full_url
            return FakeResponse(200)

        provider_setup.run_openai_check("sk-test", base_url="https://api.openai.com/v1", opener=opener)
        self.assertIn("Authorization", seen["headers"])
        self.assertEqual(seen["headers"]["Authorization"], "Bearer sk-test")
        self.assertNotIn("key=", seen["url"])


class RunGeminiCheckTests(unittest.TestCase):
    def test_ok_on_200(self):
        result = provider_setup.run_gemini_check("AIza-test", opener=opener_returning(200))
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["provider"], "gemini")

    def test_key_rejected_on_400(self):
        """Gemini's documented invalid-key status (unlike OpenAI's 401/403)."""
        result = provider_setup.run_gemini_check("AIza-test", opener=opener_raising_http_error(400))
        self.assertEqual(result["cause"]["cause"], "api_key_rejected")

    def test_key_rejected_on_401_and_403(self):
        for code in (401, 403):
            result = provider_setup.run_gemini_check("AIza-test", opener=opener_raising_http_error(code))
            self.assertEqual(result["cause"]["cause"], "api_key_rejected")

    def test_rate_limited_on_429(self):
        result = provider_setup.run_gemini_check("AIza-test", opener=opener_raising_http_error(429))
        self.assertEqual(result["cause"]["cause"], "rate_limited")

    def test_unreachable_on_network_error(self):
        result = provider_setup.run_gemini_check("AIza-test", opener=opener_unreachable())
        self.assertEqual(result["cause"]["cause"], "unreachable")

    def test_key_travels_as_query_param_not_header(self):
        seen = {}

        def opener(request, timeout):
            seen["headers"] = dict(request.headers)
            seen["url"] = request.full_url
            return FakeResponse(200)

        provider_setup.run_gemini_check("AIza-test", opener=opener)
        self.assertNotIn("Authorization", seen["headers"])
        self.assertIn("key=AIza-test", seen["url"])

    def test_key_is_url_encoded(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            return FakeResponse(200)

        provider_setup.run_gemini_check("AIza/weird key", opener=opener)
        self.assertNotIn(" ", seen["url"])
        self.assertNotIn("/", seen["url"].split("key=", 1)[1])


class StatusForTests(unittest.TestCase):
    def test_unknown_provider_raises(self):
        with self.assertRaises(provider_setup.ProviderSelectionError):
            provider_setup.status_for("not-a-real-provider", probe=lambda p: {})

    def test_cli_provider_calls_probe(self):
        calls = []

        def probe(provider):
            calls.append(provider)
            return {"installed": True, "authenticated": True, "configured": False}

        status = provider_setup.status_for("codex", probe=probe)
        self.assertEqual(calls, ["codex"])
        self.assertTrue(status["installed"])
        self.assertFalse(status["configured"])

    def test_openai_skips_probe(self):
        calls = []
        status = provider_setup.status_for("openai", probe=lambda p: calls.append(p))
        self.assertEqual(calls, [])
        self.assertNotIn("installed", status)

    def test_gemini_skips_probe(self):
        """Gemini must be treated the same as openai: no local CLI to probe."""
        calls = []
        status = provider_setup.status_for("gemini", probe=lambda p: calls.append(p))
        self.assertEqual(calls, [])
        self.assertNotIn("installed", status)
        self.assertEqual(status["provider"], "gemini")


class RecentTestsTests(unittest.TestCase):
    def test_api_provider_keyed_by_actor_only(self):
        """Gemini (like OpenAI) has no model/effort, so differing values must not
        change the lookup key -- they should all collide onto one entry."""
        store = provider_setup.RecentTests()
        store.record("actor-1", "gemini", model=None, effort=None, result={"state": "ok", "checked_at": "t"})
        found_default = store.get("actor-1", "gemini")
        found_with_model = store.get("actor-1", "gemini", model="whatever", effort="whatever")
        self.assertIsNotNone(found_default)
        self.assertEqual(found_default, found_with_model)

    def test_cli_provider_keyed_by_model_and_effort(self):
        store = provider_setup.RecentTests()
        store.record("actor-1", "codex", model="gpt-5", effort="high", result={"state": "ok", "checked_at": "t"})
        self.assertIsNotNone(store.get("actor-1", "codex", model="gpt-5", effort="high"))
        self.assertIsNone(store.get("actor-1", "codex", model="gpt-5", effort="low"))

    def test_different_actors_do_not_collide(self):
        store = provider_setup.RecentTests()
        store.record("actor-1", "gemini", None, None, {"state": "ok", "checked_at": "t"})
        self.assertIsNone(store.get("actor-2", "gemini"))

    def test_oldest_entry_evicted_past_limit(self):
        store = provider_setup.RecentTests(limit=2)
        store.record("a1", "gemini", None, None, {"state": "ok", "checked_at": "t1"})
        store.record("a2", "gemini", None, None, {"state": "ok", "checked_at": "t2"})
        store.record("a3", "gemini", None, None, {"state": "ok", "checked_at": "t3"})
        self.assertIsNone(store.get("a1", "gemini"))
        self.assertIsNotNone(store.get("a2", "gemini"))
        self.assertIsNotNone(store.get("a3", "gemini"))


class SaveAndLoadSelectionTests(unittest.TestCase):
    def test_save_rejects_unknown_provider(self):
        with self.assertRaises(provider_setup.ProviderSelectionError):
            provider_setup.save_selection(
                pathlib.Path("/tmp/unused"), "actor-1", "not-a-real-provider",
                write_json=lambda path, doc: None)

    def test_save_accepts_gemini(self):
        written = {}

        def write_json(path, doc):
            written["path"], written["doc"] = path, doc

        result = provider_setup.save_selection(
            pathlib.Path("/tmp/data-root"), "actor-1", "gemini", write_json=write_json)
        self.assertEqual(result, "gemini")
        self.assertEqual(written["doc"]["selections"]["actor-1"]["provider"], "gemini")

    def test_load_reads_back_a_saved_selection(self, tmp_path=None):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            data_root = pathlib.Path(tmp)

            def write_json(path, doc):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(doc), encoding="utf-8")

            provider_setup.save_selection(data_root, "actor-1", "gemini", write_json=write_json)
            self.assertEqual(provider_setup.load_selection(data_root, "actor-1"), "gemini")
            self.assertIsNone(provider_setup.load_selection(data_root, "unknown-actor"))


if __name__ == "__main__":
    unittest.main()
