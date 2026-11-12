"""Unit tests for tools/pathnames.py.

reject() is the boundary check both HTTP servers rely on before joining a
client-supplied run/case name onto a base directory -- a path-traversal
hole here means a request for run "../../etc" could read outside the data
root. safe_component() is the separate, narrower normalizer for values this
process mints itself. No tests existed for either before this.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

import pathnames  # noqa: E402


class RejectTests(unittest.TestCase):
    def test_ordinary_name_is_accepted(self):
        self.assertFalse(pathnames.reject("CafeMealMUJI-2026-08-29"))

    def test_real_case_name_with_spaces_and_punctuation_is_accepted(self):
        """The documented real-world regression: "Café&Meal MUJI" has a
        space, an accented letter and an ampersand -- none traversal-related
        -- and must not be rejected."""
        self.assertFalse(pathnames.reject("Café&Meal MUJI"))

    def test_cjk_name_is_accepted(self):
        self.assertFalse(pathnames.reject("長庚富康巴士"))

    def test_parent_traversal_is_rejected(self):
        self.assertTrue(pathnames.reject("../../etc"))
        self.assertTrue(pathnames.reject("../secret"))

    def test_bare_dot_and_dotdot_are_rejected(self):
        self.assertTrue(pathnames.reject("."))
        self.assertTrue(pathnames.reject(".."))

    def test_embedded_separator_is_rejected(self):
        self.assertTrue(pathnames.reject("foo/bar"))
        self.assertTrue(pathnames.reject("foo/../bar"))

    def test_absolute_path_is_rejected(self):
        self.assertTrue(pathnames.reject("/etc/passwd"))

    def test_empty_or_whitespace_is_rejected(self):
        self.assertTrue(pathnames.reject(""))
        self.assertTrue(pathnames.reject("   "))

    def test_non_string_is_rejected(self):
        self.assertTrue(pathnames.reject(None))
        self.assertTrue(pathnames.reject(123))
        self.assertTrue(pathnames.reject(["a", "b"]))

    def test_backslash_is_not_a_separator_on_posix(self):
        """A backslash is not a path separator on POSIX, so Path(...).name
        treats "foo\\..\\bar" as one literal (harmless) filename, not a
        traversal attempt -- reject() correctly lets it through. This only
        holds for this project's macOS-only deployment target; pinned down
        explicitly so a future Windows port does not inherit this assumption
        silently."""
        self.assertFalse(pathnames.reject("foo\\..\\bar"))


class SafeComponentTests(unittest.TestCase):
    def test_ordinary_name_passes_through(self):
        self.assertEqual(pathnames.safe_component("run-123", "fallback"), "run-123")

    def test_strips_directory_part_instead_of_rejecting(self):
        """Unlike reject(), this silently coerces -- "../x" becomes "x"."""
        self.assertEqual(pathnames.safe_component("../x", "fallback"), "x")

    def test_disallowed_characters_become_hyphens(self):
        result = pathnames.safe_component("a b&c", "fallback")
        self.assertNotIn(" ", result)
        self.assertNotIn("&", result)

    def test_cjk_characters_are_kept(self):
        self.assertEqual(pathnames.safe_component("長庚富康巴士", "fallback"), "長庚富康巴士")

    def test_empty_result_falls_back(self):
        self.assertEqual(pathnames.safe_component("", "fallback"), "fallback")
        self.assertEqual(pathnames.safe_component("...", "fallback"), "fallback")

    def test_truncates_to_120_characters(self):
        long_name = "a" * 300
        result = pathnames.safe_component(long_name, "fallback")
        self.assertLessEqual(len(result), 120)

    def test_leading_and_trailing_dots_and_hyphens_are_stripped(self):
        result = pathnames.safe_component("...weird-name---", "fallback")
        self.assertFalse(result.startswith(".") or result.startswith("-"))
        self.assertFalse(result.endswith(".") or result.endswith("-"))


if __name__ == "__main__":
    unittest.main()
