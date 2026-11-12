"""Regression test for the ingest/align block-separator collision.

Real bug (found 2026-10-13): ingest() joined per-file blocks with a bare
"\\n\\n---\\n\\n", and stages.py's align/source_scopes split on that exact
same string. An uploaded transcript that used "---" as its own section
divider (ordinary markdown) collided with that join: the file's block got
sliced into extra pieces, and every piece after the first lost its
"# filename · locator" header. source_scopes then had nothing to attribute
that text to, and align's fallback labelled it "原始資料" -- a placeholder
that got fed to the model as if it were a real, citable source name, which
the model then dutifully cited. In the field this took out ~748 of 750
evidence items in a single run.

ingest.BLOCK_SEPARATOR is now a sentinel no real transcript would contain
verbatim, imported by stages.py rather than redefined, so the two can never
drift apart again.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

REPO_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
if str(REPO_TOOLS) not in sys.path:
    sys.path.insert(0, str(REPO_TOOLS))

from sbp_gen import ingest, stages  # noqa: E402


class BlockSeparatorCollisionTests(unittest.TestCase):
    def test_file_content_with_markdown_horizontal_rules_survives_ingest(self):
        with_dashes = (
            "受訪者說了第一段話。\n"
            "\n---\n\n"
            "受訪者說了第二段話，中間有一條分隔線。\n"
            "\n---\n\n"
            "受訪者說了第三段話。"
        )
        with tempfile.TemporaryDirectory() as tmp:
            file_path = pathlib.Path(tmp) / "interview.md"
            file_path.write_text(with_dashes, encoding="utf-8")
            raw_material, manifest = ingest.ingest(pathlib.Path(tmp))

        self.assertEqual(manifest["summary"]["files_extracted"], 1)
        scopes = dict(stages.source_scopes(raw_material))
        self.assertIn("interview.md", scopes)
        self.assertNotIn("原始資料", scopes)
        # All three paragraphs stay attributed to the one real file.
        self.assertIn("第一段話", scopes["interview.md"])
        self.assertIn("第二段話", scopes["interview.md"])
        self.assertIn("第三段話", scopes["interview.md"])

    def test_align_block_separator_is_sourced_from_ingest(self):
        # Both constants must be the literal object ingest defines, not a
        # redefinition -- that's the only thing that guarantees they can't
        # silently drift apart again.
        self.assertIs(stages._ALIGN_BLOCK_SEPARATOR, ingest.BLOCK_SEPARATOR)
        self.assertIs(stages._SCOPE_SEPARATOR, ingest.BLOCK_SEPARATOR)

    def test_block_separator_is_not_a_plausible_transcript_line(self):
        # The whole fix rests on this never appearing in real content.
        self.assertNotEqual(ingest.BLOCK_SEPARATOR.strip(), "---")


if __name__ == "__main__":
    unittest.main()
