from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import inspect_index  # noqa: E402
from selftest import make_sample  # noqa: E402


class InspectTests(unittest.TestCase):
    def test_inspector_reports_structural_anomalies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            structure = root / "structure.json"
            chunks = root / "chunks.jsonl"
            blocks = [
                {
                    "id": f"b{index}",
                    "text": f"heading {index}",
                    "bbox": [50, 50 + index * 20, 550, 65 + index * 20],
                    "is_heading": index < 6,
                }
                for index in range(10)
            ]
            structure.write_text(
                json.dumps({
                    "pages_total": 1,
                    "pages": [{
                        "page": 1,
                        "width": 600,
                        "height": 800,
                        "blocks": blocks,
                        "tables": [],
                        "images": [{"bbox": [20, 20, 580, 700]}],
                        "dropped_margin_blocks": 8,
                    }],
                }),
                encoding="utf-8",
            )
            records = [
                {
                    "chunk_id": f"c{index}",
                    "doc_id": "doc",
                    "doc_title": "bad",
                    "page": 1,
                    "chunk_type": "section",
                    "order": index,
                    "section_path": [],
                    "char_len": 1200,
                    "text": "x" * 1200,
                    "embed_text": "x" * 1200,
                    "quote_anchor": "page=1",
                }
                for index in range(3)
            ]
            chunks.write_text(
                "\n".join(json.dumps(item) for item in records) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "inspect_index.py"),
                    str(structure),
                    str(chunks),
                    "--json",
                    "--strict",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(1, result.returncode, result.stderr)
            self.assertTrue(result.stdout, result.stderr)
            report = json.loads(result.stdout)
            self.assertGreaterEqual(len(report["issues"]), 4)
            codes = {issue["code"] for issue in report["issues"]}
            self.assertIn("missing_tables", codes)
            self.assertIn("missing_section_path", codes)

    def test_one_click_build_runs_structure_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "sample.pdf"
            make_sample(pdf)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_pdf_rag.py"),
                    str(pdf),
                    "--out-dir",
                    str(root / "index"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("section_without_path_ratio", result.stdout)

    def test_normal_three_page_contract_does_not_trigger_margin_anomaly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "sample.pdf"
            index = root / "index"
            make_sample(pdf)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_pdf_rag.py"),
                    str(pdf),
                    "--out-dir",
                    str(index),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            structure = json.loads(
                (index / "structure.json").read_text(encoding="utf-8")
            )
            chunks = [
                json.loads(line)
                for line in (index / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            report = inspect_index.inspect(structure, chunks)
            codes = {issue["code"] for issue in report["issues"]}

            self.assertNotIn("margin_removal", codes)

    def test_common_six_margin_blocks_in_three_pages_is_not_an_anomaly(self) -> None:
        blocks = [
            {"id": f"b{index}", "text": "body", "bbox": [50, 100, 500, 120], "is_heading": False}
            for index in range(17)
        ]
        structure = {
            "pages": [{
                "page": 1,
                "width": 600,
                "height": 800,
                "blocks": blocks,
                "tables": [],
                "images": [],
                "dropped_margin_blocks": 6,
            }],
        }
        chunks = [{
            "chunk_id": "c1", "doc_id": "doc", "doc_title": "contract",
            "page": 1, "chunk_type": "section", "order": 1,
            "section_path": ["chapter"], "char_len": 100,
            "text": "x" * 100, "embed_text": "x" * 100, "quote_anchor": "page=1",
        }]

        report = inspect_index.inspect(structure, chunks)
        codes = {issue["code"] for issue in report["issues"]}

        self.assertNotIn("margin_removal", codes)


if __name__ == "__main__":
    unittest.main()
