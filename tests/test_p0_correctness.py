from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_chunks  # noqa: E402
import detect_pdf_type  # noqa: E402
import extract_structure  # noqa: E402


def block(text: str, bbox: list[float], **extra: object) -> dict:
    value = {
        "id": f"p1-b-{text}",
        "text": text,
        "bbox": bbox,
        "size": 10.0,
        "bold_ratio": 0.0,
        "is_heading": False,
        "level": None,
        "in_image": False,
    }
    value.update(extra)
    return value


class LayoutTests(unittest.TestCase):
    def test_gap_clustering_returns_three_uneven_columns(self) -> None:
        blocks = [
            block("l1", [20, 20, 150, 40]),
            block("l2", [20, 50, 130, 70]),
            block("l3", [20, 80, 145, 100]),
            block("m1", [240, 20, 360, 40]),
            block("m2", [240, 50, 350, 70]),
            block("m3", [240, 80, 365, 100]),
            block("r1", [470, 20, 590, 40]),
            block("r2", [470, 50, 580, 70]),
            block("r3", [470, 80, 595, 100]),
        ]

        bands = extract_structure.detect_column_bands(blocks, 620)

        self.assertEqual(3, len(bands))
        ordered = extract_structure.order_blocks(blocks, 620, 3)
        self.assertEqual(
            ["l1", "l2", "l3", "m1", "m2", "m3", "r1", "r2", "r3"],
            [item["text"] for item in ordered],
        )

    def test_centered_title_does_not_create_false_column(self) -> None:
        blocks = [
            block("centered", [180, 20, 420, 45]),
            block("body1", [60, 80, 540, 110]),
            block("body2", [60, 120, 520, 150]),
            block("body3", [60, 160, 500, 190]),
        ]

        bands = extract_structure.detect_column_bands(blocks, 600)

        self.assertEqual(1, len(bands))


class ColumnVoteTests(unittest.TestCase):
    def test_document_column_vote_ignores_single_page_outlier(self) -> None:
        self.assertEqual(2, detect_pdf_type.choose_document_columns([2, 2, 1]))
        self.assertEqual(1, detect_pdf_type.choose_document_columns([2, 1, 1]))
        self.assertEqual(3, detect_pdf_type.choose_document_columns([3, 3, 3, 1]))


class CaptionDeduplicationTests(unittest.TestCase):
    def test_image_caption_is_not_duplicated_into_section_chunks(self) -> None:
        caption = "图 1 设备布局示意"
        data = {
            "file": "sample.pdf",
            "source_sha256": "a" * 64,
            "pages": [
                {
                    "page": 1,
                    "width": 600,
                    "height": 800,
                    "blocks": [block(caption, [100, 220, 300, 240])],
                    "tables": [],
                    "images": [
                        {
                            "id": "p1-i0",
                            "bbox": [100, 100, 300, 200],
                            "width_px": 400,
                            "height_px": 200,
                        }
                    ],
                }
            ],
        }

        chunks, _ = build_chunks.build(data, Path("missing.pdf"), 900)
        section_text = "\n".join(
            chunk["text"]
            for chunk in chunks
            if chunk["chunk_type"] == "section"
        )

        self.assertNotIn(caption, section_text)
        image = next(chunk for chunk in chunks if chunk["chunk_type"] == "image")
        self.assertEqual(caption, image["caption"])


class CrossPageTableTests(unittest.TestCase):
    def test_cross_page_table_continuation_is_merged(self) -> None:
        pages = [
            {
                "page": 1,
                "tables": [
                    {
                        "id": "p1-t0",
                        "bbox": [50, 500, 550, 750],
                        "rows": 3,
                        "cols": 3,
                        "caption": "表 1 金额",
                        "markdown": "| 阶段 | 比例 | 金额 |\n| --- | --- | --- |\n| 首付 | 30% | 100 |",
                    }
                ],
            },
            {
                "page": 2,
                "tables": [
                    {
                        "id": "p2-t0",
                        "bbox": [50, 50, 550, 250],
                        "rows": 3,
                        "cols": 3,
                        "caption": "",
                        "markdown": "| 阶段 | 比例 | 金额 |\n| --- | --- | --- |\n| 尾款 | 70% | 200 |",
                    }
                ],
            },
        ]

        merged = extract_structure.merge_cross_page_tables(pages)

        tables = merged[0]["tables"]
        self.assertEqual(1, len(tables))
        self.assertEqual([], merged[1]["tables"])
        self.assertEqual(2, tables[0]["page_end"])
        self.assertIn("首付", tables[0]["markdown"])
        self.assertIn("尾款", tables[0]["markdown"])
        separator_lines = [
            line for line in tables[0]["markdown"].splitlines()
            if line.startswith("| ---")
        ]
        self.assertEqual(1, len(separator_lines))

    def test_merged_table_chunk_exposes_page_range_anchor(self) -> None:
        data = {
            "file": "sample.pdf",
            "source_sha256": "b" * 64,
            "pages": [{
                "page": 1,
                "width": 600,
                "height": 800,
                "blocks": [],
                "images": [],
                "tables": [{
                    "id": "p1-t0",
                    "bbox": [50, 500, 550, 750],
                    "rows": 3,
                    "cols": 3,
                    "caption": "表 1",
                    "page_end": 2,
                    "markdown": "| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |",
                }],
            }],
        }

        chunks, _ = build_chunks.build(data, Path("missing.pdf"), 900)
        table = next(chunk for chunk in chunks if chunk["chunk_type"] == "table")

        self.assertEqual(2, table["page_end"])
        self.assertIn("page_end=2", table["quote_anchor"])


class MarginTests(unittest.TestCase):
    def test_two_page_footer_text_is_not_removed_as_repeated_margin(self) -> None:
        footer = [block("本合同一式两份", [50, 770, 250, 790])]
        repeats = extract_structure.detect_repeat_margins(
            [footer, footer],
            [800, 800],
        )
        self.assertEqual(set(), repeats)


class SplitLongTests(unittest.TestCase):
    def test_long_text_prefers_clause_boundaries_and_marks_hard_cut(self) -> None:
        text = (
            "第一段包含足够多的文字用于测试切分，"
            "第二段继续补充内容并保持语义，"
            "第三段完成整个句子。"
        )
        parts = build_chunks.split_long(text, 18)

        self.assertTrue(parts)
        self.assertTrue(all(part[0] for part in parts))
        self.assertTrue(all(len(part[0]) <= 18 for part in parts))
        self.assertIn("，", parts[0][0] or parts[1][0])

        hard_parts = build_chunks.split_long("甲" * 45, 18)
        self.assertTrue(all(len(part[0]) <= 18 for part in hard_parts))
        self.assertTrue(any(part[1] for part in hard_parts))

    def test_stats_reports_hard_cut_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunks.jsonl"
            record = {
                "chunk_id": "c1", "doc_id": "doc", "doc_title": "title",
                "page": 1, "chunk_type": "section", "order": 1,
                "section_path": [], "char_len": 20, "text": "x" * 20,
                "embed_text": "x" * 20, "quote_anchor": "page=1",
                "truncated": True,
            }
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "pdf_tools.py"),
                    "stats",
                    str(path),
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(1, json.loads(result.stdout)["truncated_chunks"])


class EncryptionExitCodeTests(unittest.TestCase):
    def test_encrypted_pdf_without_password_exits_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "encrypted.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "secret content", fontname="helv")
            doc.save(
                path,
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw="owner",
                user_pw="secret",
            )
            doc.close()

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "detect_pdf_type.py"),
                    str(path),
                    "--out",
                    str(Path(tmp) / "type.json"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(3, result.returncode)
            self.assertIn("密码", result.stderr)

    def test_blank_pdf_extraction_exits_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blank.pdf"
            doc = pymupdf.open()
            doc.new_page()
            doc.save(path)
            doc.close()

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "extract_structure.py"),
                    str(path),
                    "--out",
                    str(Path(tmp) / "structure.json"),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(4, result.returncode)
            self.assertIn("有效文本", result.stderr)


if __name__ == "__main__":
    unittest.main()
