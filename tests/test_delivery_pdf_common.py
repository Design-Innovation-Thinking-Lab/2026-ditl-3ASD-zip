"""Unit tests for tools/sbp_gen/delivery_pdf_common.py.

This module is the result of extracting three byte-for-byte-duplicated
pieces out of 5 delivery_*_pdf.py files. The extraction also fixed a real
drift: 3 of the 5 files let an OSError from the pandoc subprocess call
propagate raw instead of converting it to PDFRenderError. These tests pin
down the now-unified behavior, including that fix.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest
import unittest.mock as mock

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from sbp_gen import delivery_pdf_common as pdf_common  # noqa: E402


class PDFRenderErrorTests(unittest.TestCase):
    def test_message_includes_detail_when_given(self):
        err = pdf_common.PDFRenderError("some_code", "extra detail")
        self.assertEqual(str(err), "some_code: extra detail")
        self.assertEqual(err.code, "some_code")
        self.assertEqual(err.detail, "extra detail")

    def test_message_is_just_the_code_without_detail(self):
        err = pdf_common.PDFRenderError("some_code")
        self.assertEqual(str(err), "some_code")
        self.assertIsNone(err.detail)


class ResolveOutputPathTests(unittest.TestCase):
    def test_rejects_non_pdf_extension(self):
        with self.assertRaises(pdf_common.PDFRenderError) as ctx:
            pdf_common._resolve_output_path("/tmp/report.txt")
        self.assertEqual(ctx.exception.code, "pdf_output_must_have_pdf_extension")

    def test_accepts_pdf_extension_case_insensitively(self):
        with mock.patch("storage_locations.protected_canonical_data_kind", return_value=None):
            path = pdf_common._resolve_output_path("/tmp/report.PDF")
        self.assertEqual(path.suffix, ".PDF")

    def test_rejects_protected_canonical_data_path(self):
        with mock.patch("storage_locations.protected_canonical_data_kind", return_value="cases"):
            with self.assertRaises(pdf_common.PDFRenderError) as ctx:
                pdf_common._resolve_output_path("/tmp/report.pdf")
        self.assertEqual(ctx.exception.code, "pdf_output_cannot_target_canonical_data")
        self.assertEqual(ctx.exception.detail, "cases")

    def test_rejects_path_that_is_an_existing_directory(self):
        """The path must end in .pdf to even reach the directory check, so
        the fixture needs a real directory named like a PDF file."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            directory_shaped_like_a_pdf = pathlib.Path(tmp) / "report.pdf"
            directory_shaped_like_a_pdf.mkdir()
            with mock.patch("storage_locations.protected_canonical_data_kind", return_value=None):
                with self.assertRaises(pdf_common.PDFRenderError) as ctx:
                    pdf_common._resolve_output_path(directory_shaped_like_a_pdf)
        self.assertEqual(ctx.exception.code, "pdf_output_path_is_directory")


class MarkdownToHtmlTests(unittest.TestCase):
    def test_raises_when_pandoc_is_not_installed(self):
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(pdf_common.PDFRenderError) as ctx:
                pdf_common._markdown_to_html("# hi", "<style></style>")
        self.assertEqual(ctx.exception.code, "pdf_runtime_missing")

    def test_oserror_from_subprocess_is_converted_not_propagated(self):
        """The fix this extraction applies everywhere: 3 of the 5 original
        files let a raw OSError (e.g. pandoc vanishing mid-call) escape;
        this is now always caught and turned into PDFRenderError."""
        with mock.patch("shutil.which", return_value="/usr/bin/pandoc"), \
             mock.patch("subprocess.run", side_effect=OSError("boom")):
            with self.assertRaises(pdf_common.PDFRenderError) as ctx:
                pdf_common._markdown_to_html("# hi", "<style></style>")
        self.assertEqual(ctx.exception.code, "pdf_markdown_conversion_failed")

    def test_nonzero_pandoc_exit_raises_with_stderr_detail(self):
        fake_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="bad markdown input")
        with mock.patch("shutil.which", return_value="/usr/bin/pandoc"), \
             mock.patch("subprocess.run", return_value=fake_result):
            with self.assertRaises(pdf_common.PDFRenderError) as ctx:
                pdf_common._markdown_to_html("# hi", "<style></style>")
        self.assertEqual(ctx.exception.code, "pdf_markdown_conversion_failed")
        self.assertIn("bad markdown input", ctx.exception.detail)

    def test_output_missing_head_or_body_is_rejected(self):
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="not real html", stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/pandoc"), \
             mock.patch("subprocess.run", return_value=fake_result):
            with self.assertRaises(pdf_common.PDFRenderError) as ctx:
                pdf_common._markdown_to_html("# hi", "<style></style>")
        self.assertEqual(ctx.exception.code, "pdf_markdown_conversion_invalid_output")

    def test_page_css_is_injected_before_closing_head_tag(self):
        """This is the whole point of taking page_css as a parameter: each
        of the 5 callers must get ITS OWN css injected, not some other
        caller's."""
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="<html><head></head><body>hi</body></html>", stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/pandoc"), \
             mock.patch("subprocess.run", return_value=fake_result):
            html = pdf_common._markdown_to_html("# hi", "<style>.marker{}</style>")
        # The real implementation inserts a newline between the css and
        # </head> (see the "page_css + \n</head>" replacement) -- not
        # directly adjacent text, so check position rather than exact
        # adjacency.
        self.assertIn("<style>.marker{}</style>", html)
        self.assertLess(html.index("<style>.marker{}</style>"), html.index("</head>"))

    def test_different_callers_get_different_css_not_a_shared_default(self):
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="<html><head></head><body>hi</body></html>", stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/pandoc"), \
             mock.patch("subprocess.run", return_value=fake_result):
            html_a = pdf_common._markdown_to_html("# hi", "<style>A</style>")
            html_b = pdf_common._markdown_to_html("# hi", "<style>B</style>")
        self.assertIn("<style>A</style>", html_a)
        self.assertNotIn("<style>B</style>", html_a)
        self.assertIn("<style>B</style>", html_b)
        self.assertNotIn("<style>A</style>", html_b)


if __name__ == "__main__":
    unittest.main()
