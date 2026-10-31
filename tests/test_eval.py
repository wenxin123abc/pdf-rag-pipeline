from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class EvalTests(unittest.TestCase):
    def test_eval_reports_recall_mrr_pages_values_and_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chunks = root / "chunks.jsonl"
            labels = root / "labels.jsonl"
            records = [
                {
                    "chunk_id": "c1", "doc_id": "doc", "doc_title": "Guide",
                    "page": 1, "chunk_type": "section", "order": 1,
                    "section_path": [], "text": "payment terms alpha",
                    "embed_text": "payment terms alpha", "quote_anchor": "page=1",
                },
                {
                    "chunk_id": "c2", "doc_id": "doc", "doc_title": "Guide",
                    "page": 2, "chunk_type": "table", "order": 2,
                    "section_path": [], "text": "stage share 30% advance",
                    "embed_text": "stage share 30% advance", "quote_anchor": "page=2",
                },
                {
                    "chunk_id": "c3", "doc_id": "doc", "doc_title": "Guide",
                    "page": 3, "chunk_type": "section", "order": 3,
                    "section_path": [], "text": "delivery acceptance terms",
                    "embed_text": "delivery acceptance terms", "quote_anchor": "page=3",
                },
            ]
            chunks.write_text(
                "\n".join(json.dumps(item) for item in records) + "\n",
                encoding="utf-8",
            )
            labels.write_text(
                "\n".join([
                    json.dumps({
                        "question": "payment terms",
                        "gold_chunk_ids": ["c1"],
                        "gold_pages": [1],
                        "answer_type": "fact",
                    }),
                    json.dumps({
                        "question": "advance share",
                        "gold_chunk_ids": ["c2"],
                        "gold_pages": [2],
                        "answer_type": "table",
                        "expected_values": ["30%"],
                    }),
                ]) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "eval.py"),
                    str(labels),
                    str(chunks),
                    "--k",
                    "3",
                    "--json",
                    "--min-recall",
                    "0.9",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(1.0, report["recall@3"])
            self.assertEqual(1.0, report["mrr"])
            self.assertEqual(1.0, report["page_accuracy"])
            self.assertEqual(1.0, report["table_value_match"])
            self.assertIn("fact", report["type_breakdown"])
            self.assertIn("table", report["type_breakdown"])

    def test_template_command_writes_thirty_label_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chunks = root / "chunks.jsonl"
            template = root / "labels.template.jsonl"
            records = [
                {
                    "chunk_id": f"c{index}", "doc_id": "doc", "doc_title": "Guide",
                    "page": index + 1, "chunk_type": "section", "order": index,
                    "section_path": ["chapter"], "text": f"question source {index}",
                    "embed_text": f"question source {index}", "quote_anchor": f"page={index + 1}",
                }
                for index in range(40)
            ]
            chunks.write_text(
                "\n".join(json.dumps(item) for item in records) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "eval.py"),
                    "--template-out",
                    str(template),
                    str(chunks),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            rows = [
                json.loads(line)
                for line in template.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(30, len(rows))


if __name__ == "__main__":
    unittest.main()
