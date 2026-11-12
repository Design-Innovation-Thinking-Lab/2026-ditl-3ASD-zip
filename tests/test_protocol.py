"""Unit tests for tools/cli_runners/protocol.py.

Covers _strict()/_restore() (the schema roundtrip that lets a CLI's
structured-output flag accept this project's schemas), reply_schema(), and
parse_reply() -- the function that structurally forbids a CLI runner from
ever supplying source content directly (SOURCE_CONTENT_KEYS), which is the
actual enforcement behind the "runner-side search can only ever surface
URLs" guarantee the module's own docstring describes.
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from cli_runners import protocol  # noqa: E402


class StrictSchemaTests(unittest.TestCase):
    def test_object_with_optional_field_becomes_nullable_and_required(self):
        schema = {"type": "object", "properties": {
            "a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a"]}
        strict = protocol._strict(schema)
        self.assertEqual(strict["additionalProperties"], False)
        self.assertEqual(set(strict["required"]), {"a", "b"})
        self.assertEqual(strict["properties"]["a"], {"type": "string"})
        self.assertIn("anyOf", strict["properties"]["b"])

    def test_free_object_with_no_properties_becomes_a_json_string(self):
        """An object schema with no declared properties cannot be expressed
        under strict mode, so it must travel as a JSON-encoded string."""
        schema = {"type": "object"}
        strict = protocol._strict(schema)
        self.assertEqual(strict["type"], "string")

    def test_array_recurses_into_items(self):
        schema = {"type": "array", "items": {"type": "object", "properties": {"x": {"type": "string"}},
                                              "required": ["x"]}}
        strict = protocol._strict(schema)
        self.assertEqual(strict["type"], "array")
        self.assertEqual(strict["items"]["properties"]["x"], {"type": "string"})

    def test_nested_object_is_also_made_strict(self):
        schema = {"type": "object", "properties": {
            "outer": {"type": "object", "properties": {"inner": {"type": "string"}}, "required": ["inner"]}},
            "required": ["outer"]}
        strict = protocol._strict(schema)
        self.assertEqual(strict["properties"]["outer"]["additionalProperties"], False)

    def test_restore_is_the_inverse_of_strict_for_required_fields(self):
        schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
        value = {"a": "hello"}
        restored = protocol._restore(schema, value)
        self.assertEqual(restored, {"a": "hello"})

    def test_restore_drops_null_optional_fields(self):
        """_restore must undo the nullable-wrapping _strict introduced: a
        null optional field should disappear, not round-trip as None."""
        schema = {"type": "object", "properties": {
            "a": {"type": "string"}, "b": {"type": "string"}}, "required": ["a"]}
        restored = protocol._restore(schema, {"a": "hello", "b": None})
        self.assertEqual(restored, {"a": "hello"})

    def test_restore_decodes_a_json_encoded_free_object(self):
        schema = {"type": "object"}
        restored = protocol._restore(schema, json.dumps({"anything": "goes"}))
        self.assertEqual(restored, {"anything": "goes"})

    def test_restore_rejects_malformed_json_for_a_free_object(self):
        schema = {"type": "object"}
        with self.assertRaises(protocol.RunnerError):
            protocol._restore(schema, "{not valid json")

    def test_full_roundtrip_through_strict_and_restore(self):
        """_restore takes the ORIGINAL schema (as real callers do via
        _result_schema(body)), not the strict one -- it undoes what _strict
        did to the *value*, using the original shape as its map."""
        original = {"type": "object", "properties": {
            "name": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string"},
        }, "required": ["name", "tags"]}
        value = {"name": "alice", "tags": ["a", "b"]}
        # Simulate what a structured-output model would actually send back:
        # every declared property present, optional ones null when omitted.
        sent_back = {"name": "alice", "tags": ["a", "b"], "note": None}
        restored = protocol._restore(original, sent_back)
        self.assertEqual(restored, value)


class ReplySchemaTests(unittest.TestCase):
    def test_envelope_requires_exactly_one_of_fetch_or_result(self):
        body = {"text": {"format": {"schema": {"type": "object", "properties": {"x": {"type": "string"}},
                                                "required": ["x"]}}}}
        schema = protocol.reply_schema(body)
        self.assertEqual(set(schema["required"]), {"fetch", "result"})


class ParseReplyTests(unittest.TestCase):
    def _body(self):
        return {"text": {"format": {"schema": {
            "type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}}}}

    def test_fetch_reply_must_be_a_non_empty_list_of_strings(self):
        """A valid `fetch` reply is shaped into a function_call the research
        loop can dispatch -- it never reaches the caller as raw URLs."""
        text = json.dumps({"fetch": ["https://example.com"], "result": None})
        relay = protocol.parse_reply(text, self._body())
        self.assertEqual(relay["output"][0]["type"], "function_call")
        self.assertIn("https://example.com", relay["output"][0]["arguments"])

    def test_both_fetch_and_result_present_is_malformed(self):
        text = json.dumps({"fetch": ["https://example.com"], "result": {"answer": "x"}})
        with self.assertRaises(protocol.RunnerError):
            protocol.parse_reply(text, self._body())

    def test_neither_fetch_nor_result_is_malformed(self):
        text = json.dumps({"fetch": None, "result": None})
        with self.assertRaises(protocol.RunnerError):
            protocol.parse_reply(text, self._body())

    def test_empty_fetch_list_is_malformed(self):
        text = json.dumps({"fetch": [], "result": None})
        with self.assertRaises(protocol.RunnerError):
            protocol.parse_reply(text, self._body())

    def test_result_carrying_source_content_is_refused(self):
        """The structural guarantee the module's docstring promises: a
        runner cannot smuggle a source's fetched body back through `result`."""
        text = json.dumps({"fetch": None, "result": {
            "answer": "x", "sources": [{"url": "https://example.com", "content": "the whole page text"}]}})
        with self.assertRaises(protocol.RunnerError) as ctx:
            protocol.parse_reply(text, self._body())
        self.assertEqual(ctx.exception.code, "runner_reply_supplies_source_content")

    def test_result_with_only_a_url_in_sources_is_accepted(self):
        text = json.dumps({"fetch": None, "result": {
            "answer": "x", "sources": [{"url": "https://example.com"}]}})
        relay = protocol.parse_reply(text, self._body())
        self.assertEqual(relay["output"][0]["type"], "message")

    def test_reply_wrapped_in_markdown_code_fence_is_still_parsed(self):
        payload = json.dumps({"fetch": None, "result": {"answer": "x"}})
        text = f"```json\n{payload}\n```"
        relay = protocol.parse_reply(text, self._body())
        self.assertEqual(relay["output"][0]["type"], "message")

    def test_non_json_text_is_malformed(self):
        with self.assertRaises(protocol.RunnerError):
            protocol.parse_reply("this is not json at all", self._body())


class BuildPromptTests(unittest.TestCase):
    def test_completion_prompt_includes_system_and_user(self):
        prompt = protocol.build_completion_prompt({"system": "be helpful", "user": "hello"})
        self.assertIn("be helpful", prompt)
        self.assertIn("hello", prompt)

    def test_completion_prompt_tolerates_missing_fields(self):
        prompt = protocol.build_completion_prompt({})
        self.assertIsInstance(prompt, str)


class HandleTests(unittest.TestCase):
    def test_completion_request_calls_runner_and_wraps_reply(self):
        def fake_runner(prompt, *, output_schema, web_search, model, timeout):
            return protocol.RunnerReply(text="the answer", tokens=42, model="fake-model")

        result = protocol.handle({"kind": "completion", "system": "s", "user": "u"}, fake_runner)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["content"], "the answer")
        self.assertEqual(result["tokens"], 42)

    def test_completion_request_with_empty_reply_raises(self):
        def fake_runner(prompt, *, output_schema, web_search, model, timeout):
            return protocol.RunnerReply(text="   ")

        with self.assertRaises(protocol.RunnerError) as ctx:
            protocol.handle({"kind": "completion", "system": "s", "user": "u"}, fake_runner)
        self.assertEqual(ctx.exception.code, "runner_empty_reply")

    def test_unknown_kind_is_malformed(self):
        with self.assertRaises(protocol.RunnerError):
            protocol.handle({"kind": "not-a-real-kind"}, lambda *a, **k: None)

    def test_negative_timeout_is_timed_out(self):
        with self.assertRaises(protocol.RunnerError) as ctx:
            protocol.handle({"kind": "completion", "timeout_seconds": -1}, lambda *a, **k: None)
        self.assertEqual(ctx.exception.code, "runner_timed_out")

    def test_zero_timeout_is_treated_as_the_default_not_as_timed_out(self):
        """`0 or DEFAULT_TIMEOUT_SECONDS` evaluates to the default in Python
        (0 is falsy) -- passing 0 does NOT mean "fail immediately" here, it
        silently falls back to the normal 600s budget. Documented so this
        surprising `or`-based default doesn't get "fixed" by accident later
        without someone noticing the behavior actually changed."""
        def fake_runner(prompt, *, output_schema, web_search, model, timeout):
            return protocol.RunnerReply(text="ok")

        result = protocol.handle({"kind": "completion", "timeout_seconds": 0}, fake_runner)
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
