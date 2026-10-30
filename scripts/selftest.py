#!/usr/bin/env python3
"""Create a temporary PDF and verify the complete local pipeline."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf


SCRIPT_DIR = Path(__file__).resolve().parent


def run(*args: str) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(
            f"command failed: {args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def make_sample(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 70), "TEST CONTRACT", fontsize=20, fontname="helv")
    page.insert_text((50, 110), "1 PAYMENT", fontsize=14, fontname="helv")
    page.insert_textbox(
        pymupdf.Rect(50, 130, 540, 220),
        "The payment terms require a thirty percent advance payment "
        "within ten business days after the contract takes effect.",
        fontsize=10,
        fontname="helv",
    )

    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
        pymupdf.Rect(50, 60, 540, 100),
        "This continued paragraph belongs to the same payment section "
        "even though the heading appears on the previous page.",
        fontsize=10,
        fontname="helv",
    )
    rows = [
        ["Stage", "Share", "Condition"],
        ["Advance", "30%", "Contract effective"],
        ["Delivery", "70%", "Acceptance passed"],
    ]
    x0, y0, widths, height = 50, 120, [120, 80, 280], 28
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            rect = pymupdf.Rect(
                x0 + sum(widths[:col_index]),
                y0 + row_index * height,
                x0 + sum(widths[:col_index + 1]),
                y0 + (row_index + 1) * height,
            )
            page.draw_rect(rect, color=(0.4, 0.4, 0.4), width=0.7)
            page.insert_textbox(rect + (3, 4, -3, -3), cell, fontsize=9, fontname="helv")
    page.insert_text((50, 240), "Table 1: Payment schedule", fontsize=8, fontname="helv")

    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), False)
    pixmap.clear_with(220)
    page.insert_image(pymupdf.Rect(70, 300, 250, 420), pixmap=pixmap)
    page.insert_text((70, 440), "Figure 1: Diagram", fontsize=8, fontname="helv")
    doc.save(path)
    doc.close()


def make_two_column_sample(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for column, x in enumerate((50, 315)):
        for row in range(3):
            page.insert_textbox(
                pymupdf.Rect(x, 80 + row * 110, x + 220, 170 + row * 110),
                f"Column {column + 1} paragraph {row + 1}. "
                "This text is used only to verify vertical gutter detection.",
                fontsize=10,
                fontname="helv",
            )
    doc.save(path)
    doc.close()


def make_image_only_sample(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 400), False)
    pixmap.clear_with(180)
    page.insert_image(pymupdf.Rect(40, 40, 555, 800), pixmap=pixmap)
    doc.save(path)
    doc.close()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="pdf-rag-selftest-") as tmp:
        root = Path(tmp)
        pdf = root / "sample.pdf"
        type_report = root / "type.json"
        structure = root / "structure.json"
        chunks = root / "chunks.jsonl"
        make_sample(pdf)

        run(str(SCRIPT_DIR / "detect_pdf_type.py"), str(pdf), "--out", str(type_report))
        run(
            str(SCRIPT_DIR / "extract_structure.py"), str(pdf),
            "--out", str(structure),
        )
        run(
            str(SCRIPT_DIR / "build_chunks.py"), str(structure),
            "--out", str(chunks),
        )
        run(str(SCRIPT_DIR / "pdf_tools.py"), "stats", str(chunks), "--json")

        parsed = [
            json.loads(line)
            for line in chunks.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        page_two_sections = [
            c for c in parsed
            if c["chunk_type"] == "section" and c["page"] == 2
        ]
        if not page_two_sections or not page_two_sections[0]["section_path"]:
            raise AssertionError("跨页章节上下文未保留")
        if not any(c["chunk_type"] == "table" for c in parsed):
            raise AssertionError("表格未被抽取")

        search = run(
            str(SCRIPT_DIR / "pdf_tools.py"), "search", str(chunks),
            "payment terms", "--json",
        )
        hits = json.loads(search.stdout)
        if not hits or hits[0]["page"] != 1:
            raise AssertionError("检索未命中预期页面")
        run(
            str(SCRIPT_DIR / "pdf_tools.py"), "quote", str(chunks),
            "--chunk-id", hits[0]["chunk_id"],
        )
        run(str(SCRIPT_DIR / "pdf_tools.py"), "table", str(chunks), "--page", "2")
        run(str(SCRIPT_DIR / "pdf_tools.py"), "image", str(chunks), "--page", "2")
        no_match = run(
            str(SCRIPT_DIR / "pdf_tools.py"), "search", str(chunks),
            "definitely_not_present_42", "--json",
        )
        if json.loads(no_match.stdout) != []:
            raise AssertionError("无匹配查询应返回空数组")

        image = next((c for c in parsed if c["chunk_type"] == "image"), None)
        if image:
            mapping = root / "image_descriptions.json"
            mapping.write_text(
                json.dumps({image["image_id"]: "A plain blue-gray diagram."}),
                encoding="utf-8",
            )
            run(
                str(SCRIPT_DIR / "apply_visual_descriptions.py"),
                str(chunks),
                str(mapping),
            )
            stats = run(
                str(SCRIPT_DIR / "pdf_tools.py"), "stats", str(chunks), "--json"
            )
            if json.loads(stats.stdout)["visual_descriptions_pending"] != 0:
                raise AssertionError("视觉描述回填后仍有待处理项")

        one_click = root / "one-click"
        run(
            str(SCRIPT_DIR / "build_pdf_rag.py"), str(pdf),
            "--out-dir", str(one_click),
        )
        if not (one_click / "chunks.jsonl").exists():
            raise AssertionError("一键构建没有生成 chunks.jsonl")

        two_column_pdf = root / "two-column.pdf"
        two_column_report = root / "two-column.json"
        make_two_column_sample(two_column_pdf)
        run(
            str(SCRIPT_DIR / "detect_pdf_type.py"),
            str(two_column_pdf),
            "--out", str(two_column_report),
        )
        columns = json.loads(
            two_column_report.read_text(encoding="utf-8")
        )["columns"]["max_detected"]
        if columns != 2:
            raise AssertionError(f"双栏检测失败: {columns}")

        scanned_pdf = root / "scanned.pdf"
        scanned_report = root / "scanned.json"
        make_image_only_sample(scanned_pdf)
        run(
            str(SCRIPT_DIR / "detect_pdf_type.py"),
            str(scanned_pdf),
            "--out", str(scanned_report),
        )
        if json.loads(
            scanned_report.read_text(encoding="utf-8")
        )["doc_type"] != "scanned":
            raise AssertionError("纯图片 PDF 未识别为 scanned")
        scanned_out = root / "scanned-out"
        blocked = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "build_pdf_rag.py"),
                str(scanned_pdf),
                "--out-dir", str(scanned_out),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        if blocked.returncode != 2 or (scanned_out / "chunks.jsonl").exists():
            raise AssertionError("扫描件应停止并返回退出码 2")

        print("PASS: PDF type detection, structure extraction, chunking, search,")
        print("      cross-page sections, two-column/scanned detection, visual annotations")


if __name__ == "__main__":
    main()
