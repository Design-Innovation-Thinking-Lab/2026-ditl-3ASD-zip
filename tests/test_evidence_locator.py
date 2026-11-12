"""Unit tests for tools/evidence_locator.py.

locate_evidence() is the actual mechanism behind this project's core
guarantee -- "every AI judgement can be traced back to the source quote it
came from" -- so a bug here means a claim gets silently attached to the
wrong evidence, or no evidence at all. Uses a temp directory as the data
root via configure_data_root(), exercising each of the three lookup paths
the module's own docstring documents (run-local, run.json pointer, unique
file in the case directory) plus the "ambiguous" refusal.
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

import yaml

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

import evidence_locator  # noqa: E402


class EvidenceLocatorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_root = pathlib.Path(self._tmp.name)
        evidence_locator.configure_data_root(self.data_root)
        self.runs_dir = self.data_root / "runs"
        self.cases_dir = self.data_root / "cases"
        self.runs_dir.mkdir(parents=True)
        self.cases_dir.mkdir(parents=True)

    def tearDown(self):
        evidence_locator.configure_data_root(None)  # restore the module default
        self._tmp.cleanup()

    def _write_yaml(self, path: pathlib.Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data), encoding="utf-8")

    def test_run_local_evidence_is_found_first(self):
        run_dir = self.runs_dir / "run-1"
        run_dir.mkdir()
        self._write_yaml(run_dir / "evidence.yaml", [{"id": "e1", "quote": "hello"}])
        records, source = evidence_locator.locate_evidence(run_dir)
        self.assertEqual(source, "run_local")
        self.assertEqual(records, [{"id": "e1", "quote": "hello"}])

    def test_run_json_pointer_is_followed_when_no_local_copy(self):
        run_dir = self.runs_dir / "run-2"
        run_dir.mkdir()
        self._write_yaml(self.cases_dir / "mycase" / "evidence_custom.yaml", [{"id": "e2"}])
        (run_dir / "run.json").write_text(
            json.dumps({"from_evidence": "cases/mycase/evidence_custom.yaml"}), encoding="utf-8")
        records, source = evidence_locator.locate_evidence(run_dir)
        self.assertEqual(source, "run_json_pointer")
        self.assertEqual(records, [{"id": "e2"}])

    def test_nested_pointer_form_is_also_read(self):
        """Older runs record the pointer under inputs.evidence_yaml instead
        of the top-level from_evidence key -- both forms must resolve."""
        run_dir = self.runs_dir / "run-3"
        run_dir.mkdir()
        self._write_yaml(self.cases_dir / "othercase" / "evidence.yaml", [{"id": "e3"}])
        (run_dir / "run.json").write_text(
            json.dumps({"inputs": {"evidence_yaml": "cases/othercase/evidence.yaml"}}), encoding="utf-8")
        records, source = evidence_locator.locate_evidence(run_dir)
        self.assertEqual(source, "run_json_pointer")
        self.assertEqual(records, [{"id": "e3"}])

    def test_falls_back_to_unique_file_in_case_directory(self):
        run_dir = self.runs_dir / "run-4"
        run_dir.mkdir()
        self._write_yaml(run_dir / "case.yaml", {"case_id": "case-xyz"})
        self._write_yaml(self.cases_dir / "case-xyz" / "case.yaml", {"case_id": "case-xyz"})
        self._write_yaml(self.cases_dir / "case-xyz" / "evidence.yaml", [{"id": "e4"}])
        records, source = evidence_locator.locate_evidence(run_dir)
        self.assertEqual(source, "case_unique")
        self.assertEqual(records, [{"id": "e4"}])

    def test_case_id_falls_back_to_run_json_when_no_local_case_yaml(self):
        """A run with no run-local case.yaml must still resolve its case
        through run.json's case_id -- the real-world gap the module's
        docstring calls out by name."""
        run_dir = self.runs_dir / "run-5"
        run_dir.mkdir()
        (run_dir / "run.json").write_text(json.dumps({"case_id": "case-abc"}), encoding="utf-8")
        self._write_yaml(self.cases_dir / "case-abc" / "case.yaml", {"case_id": "case-abc"})
        self._write_yaml(self.cases_dir / "case-abc" / "evidence.yaml", [{"id": "e5"}])
        records, source = evidence_locator.locate_evidence(run_dir)
        self.assertEqual(source, "case_unique")

    def test_two_candidate_files_return_ambiguous_not_a_guess(self):
        """Attaching the wrong verbatim evidence file to a claim is worse
        than showing none at all -- this must refuse, never pick one."""
        run_dir = self.runs_dir / "run-6"
        run_dir.mkdir()
        self._write_yaml(run_dir / "case.yaml", {"case_id": "case-dup"})
        self._write_yaml(self.cases_dir / "case-dup" / "case.yaml", {"case_id": "case-dup"})
        self._write_yaml(self.cases_dir / "case-dup" / "evidence_a.yaml", [{"id": "a"}])
        self._write_yaml(self.cases_dir / "case-dup" / "evidence_b.yaml", [{"id": "b"}])
        records, source = evidence_locator.locate_evidence(run_dir)
        self.assertEqual(source, "ambiguous")
        self.assertIsNone(records)

    def test_nothing_resolvable_reports_not_found(self):
        run_dir = self.runs_dir / "run-7"
        run_dir.mkdir()
        records, source = evidence_locator.locate_evidence(run_dir)
        self.assertEqual(source, "not_found")
        self.assertIsNone(records)

    def test_malformed_yaml_is_treated_as_missing_not_a_crash(self):
        run_dir = self.runs_dir / "run-8"
        run_dir.mkdir()
        (run_dir / "evidence.yaml").write_text("not: valid: yaml: [", encoding="utf-8")
        records, source = evidence_locator.locate_evidence(run_dir)
        # The malformed local file must not satisfy "run_local"; it falls
        # through to whatever the remaining lookups find (here: not_found).
        self.assertIsNone(records)
        self.assertEqual(source, "not_found")

    def test_evidence_yaml_that_is_not_a_list_is_rejected(self):
        run_dir = self.runs_dir / "run-9"
        run_dir.mkdir()
        self._write_yaml(run_dir / "evidence.yaml", {"not": "a list"})
        records, source = evidence_locator.locate_evidence(run_dir)
        self.assertIsNone(records)
        self.assertEqual(source, "not_found")


if __name__ == "__main__":
    unittest.main()
