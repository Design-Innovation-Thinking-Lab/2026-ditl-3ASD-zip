"""Unit tests for tools/sbp_review/review_upload.py.

The first genuinely standalone domain extracted out of server.py (as
opposed to review_jobs.py, which held shared cross-domain state). Covers
multipart parsing, intake naming, and saving uploaded files -- including
the path-traversal guard in _save_upload and the uploads_dir/data_root
parameters that replace what used to be a load-time import of server.py's
rebindable UPLOADS_DIR/DATA_ROOT globals.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from sbp_review import review_upload  # noqa: E402


def _build_multipart(parts: list[tuple[str, str, bytes]], fields: dict[str, str] | None = None) -> tuple[bytes, str]:
    """parts: list of (field_name, filename, content). Returns (body, content_type)."""
    boundary = "----testboundary123"
    lines = []
    for field_name, filename, content in parts:
        lines.append(f"--{boundary}".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode())
        lines.append(b"Content-Type: application/octet-stream")
        lines.append(b"")
        lines.append(content)
    for name, value in (fields or {}).items():
        lines.append(f"--{boundary}".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        lines.append(b"")
        lines.append(value.encode("utf-8"))
    lines.append(f"--{boundary}--".encode())
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


class ParseMultipartTests(unittest.TestCase):
    def test_extracts_filename_and_content(self):
        body, content_type = _build_multipart([("file1", "interview.txt", b"hello world")])
        files = review_upload._parse_multipart(content_type, body)
        self.assertEqual(len(files), 1)
        name, content = files[0]
        self.assertEqual(name, "interview.txt")
        self.assertEqual(content, b"hello world")

    def test_multiple_files_all_extracted(self):
        body, content_type = _build_multipart([
            ("file1", "a.txt", b"AAA"), ("file2", "b.txt", b"BBB"),
        ])
        files = review_upload._parse_multipart(content_type, body)
        self.assertEqual(len(files), 2)

    def test_unsafe_filename_is_sanitized(self):
        body, content_type = _build_multipart([("file1", "../../etc/passwd", b"x")])
        files = review_upload._parse_multipart(content_type, body)
        name, _ = files[0]
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)

    def test_original_names_receives_display_label(self):
        body, content_type = _build_multipart([("file1", "weird/../path.txt", b"x")])
        original_names: list[str] = []
        review_upload._parse_multipart(content_type, body, original_names)
        self.assertEqual(original_names, ["path.txt"])

    def test_text_fields_are_captured_separately_from_files(self):
        body, content_type = _build_multipart(
            [("file1", "a.txt", b"x")], fields={"name": "我的專案"})
        fields: dict = {}
        review_upload._parse_multipart(content_type, body, fields=fields)
        self.assertEqual(fields.get("name"), "我的專案")


class IntakeNameFromFilesTests(unittest.TestCase):
    def test_uses_first_filename_without_extension(self):
        self.assertEqual(review_upload._intake_name_from_files(["interview-01.txt", "other.txt"]), "interview-01")

    def test_empty_list_falls_back_to_default(self):
        self.assertEqual(review_upload._intake_name_from_files([]), "case-intake")

    def test_collapses_internal_whitespace(self):
        self.assertEqual(review_upload._intake_name_from_files(["my   file  name.txt"]), "my file name")


class UnusedIntakeNameTests(unittest.TestCase):
    def test_returns_name_unchanged_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads_dir = pathlib.Path(tmp)
            self.assertEqual(review_upload._unused_intake_name("fresh-name", uploads_dir=uploads_dir), "fresh-name")

    def test_appends_suffix_when_name_taken(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads_dir = pathlib.Path(tmp)
            (uploads_dir / "taken").mkdir()
            result = review_upload._unused_intake_name("taken", uploads_dir=uploads_dir)
            self.assertEqual(result, "taken-2")

    def test_keeps_incrementing_past_multiple_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads_dir = pathlib.Path(tmp)
            (uploads_dir / "taken").mkdir()
            (uploads_dir / "taken-2").mkdir()
            (uploads_dir / "taken-3").mkdir()
            result = review_upload._unused_intake_name("taken", uploads_dir=uploads_dir)
            self.assertEqual(result, "taken-4")


class SaveUploadTests(unittest.TestCase):
    def test_writes_files_under_uploads_dir_and_returns_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = pathlib.Path(tmp)
            uploads_dir = data_root / "uploads"
            result = review_upload._save_upload(
                "my-case", [("a.txt", b"hello")], uploads_dir=uploads_dir, data_root=data_root)
            self.assertEqual(result["intake_id"], "my-case")
            written = uploads_dir / "my-case" / "raw" / "a.txt"
            self.assertTrue(written.is_file())
            self.assertEqual(written.read_bytes(), b"hello")
            self.assertEqual(result["files"], ["uploads/my-case/raw/a.txt"])

    def test_duplicate_filenames_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = pathlib.Path(tmp)
            uploads_dir = data_root / "uploads"
            result = review_upload._save_upload(
                "dup-case", [("a.txt", b"first"), ("a.txt", b"second")],
                uploads_dir=uploads_dir, data_root=data_root)
            raw_dir = uploads_dir / "dup-case" / "raw"
            contents = {p.name: p.read_bytes() for p in raw_dir.iterdir()}
            self.assertEqual(len(contents), 2)
            self.assertIn(b"first", contents.values())
            self.assertIn(b"second", contents.values())

    def test_uses_current_uploads_dir_not_a_stale_captured_one(self):
        """The whole reason this takes uploads_dir as a parameter: calling
        it twice with two different directories must write to each one,
        proving there is no load-time-captured default being reused."""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            root_a, root_b = pathlib.Path(tmp_a), pathlib.Path(tmp_b)
            review_upload._save_upload("case", [("a.txt", b"x")], uploads_dir=root_a / "u", data_root=root_a)
            review_upload._save_upload("case", [("a.txt", b"x")], uploads_dir=root_b / "u", data_root=root_b)
            self.assertTrue((root_a / "u" / "case" / "raw" / "a.txt").is_file())
            self.assertTrue((root_b / "u" / "case" / "raw" / "a.txt").is_file())


class WriteIntakeCaseTests(unittest.TestCase):
    def test_writes_a_placeholder_case_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = pathlib.Path(tmp)
            uploads_dir = data_root / "uploads"
            (uploads_dir / "my-case").mkdir(parents=True)
            saved = {"intake_id": "my-case"}
            path = review_upload._write_intake_case(
                saved, label="我的專案", uploads_dir=uploads_dir, data_root=data_root)
            case_file = uploads_dir / "my-case" / "case.yaml"
            self.assertTrue(case_file.is_file())
            content = case_file.read_text(encoding="utf-8")
            self.assertIn("upload-my-case", content)
            self.assertIn("我的專案", content)
            self.assertEqual(path, "uploads/my-case/case.yaml")


if __name__ == "__main__":
    unittest.main()
