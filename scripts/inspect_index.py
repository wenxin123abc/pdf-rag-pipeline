#!/usr/bin/env python3
"""Detect likely PDF structure and chunking quality problems."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pdf_tools


def inspect(structure: dict, chunks: list[dict]) -> dict:
    pages = structure.get("pages") or []
    blocks = [
        block
        for page in pages
        for block in (page.get("blocks") or [])
    ]
    heading_count = sum(1 for block in blocks if block.get("is_heading"))
    heading_ratio = heading_count / max(len(blocks), 1)

    image_area = 0.0
    page_area = 0.0
    for page in pages:
        width = float(page.get("width") or 0)
        height = float(page.get("height") or 0)
        page_area += max(0.0, width * height)
        for image in page.get("images") or []:
            bbox = image.get("bbox") or [0, 0, 0, 0]
            image_area += max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    image_coverage = min(1.0, image_area / page_area) if page_area else 0.0

    structure_tables = sum(len(page.get("tables") or []) for page in pages)
    chunk_tables = sum(1 for chunk in chunks if chunk.get("chunk_type") == "table")
    sections = [chunk for chunk in chunks if chunk.get("chunk_type") == "section"]
    no_path = sum(1 for chunk in sections if not chunk.get("section_path"))
    section_without_path = no_path / max(len(sections), 1)
    average_length = sum(
        int(chunk.get("char_len") or len(chunk.get("text", ""))) for chunk in chunks
    ) / max(len(chunks), 1)
    dropped_margin = sum(int(page.get("dropped_margin_blocks") or 0) for page in pages)
    margin_ratio = dropped_margin / max(len(blocks) + dropped_margin, 1)

    metrics = {
        "pages": len(pages),
        "blocks": len(blocks),
        "headings": heading_count,
        "heading_ratio": round(heading_ratio, 4),
        "image_coverage": round(image_coverage, 4),
        "structure_tables": structure_tables,
        "table_chunks": chunk_tables,
        "chunks": len(chunks),
        "average_chunk_length": round(average_length, 2),
        "section_without_path_ratio": round(section_without_path, 4),
        "dropped_margin_blocks": dropped_margin,
        "dropped_margin_ratio": round(margin_ratio, 4),
    }
    issues = []
    if len(blocks) >= 5 and heading_ratio > 0.45:
        issues.append({
            "code": "heading_ratio",
            "severity": "high",
            "message": "标题数接近或超过文本块的一半，段落重建可能失败",
        })
    if structure_tables == 0 and chunk_tables == 0 and image_coverage > 0.3:
        issues.append({
            "code": "missing_tables",
            "severity": "medium",
            "message": "图片覆盖率较高但未检测到表格，复杂表格可能被当作图片",
        })
    if average_length > 1000:
        issues.append({
            "code": "long_chunks",
            "severity": "medium",
            "message": "平均 chunk 长度超过 1000，建议降低 --max-chars",
        })
    if sections and section_without_path > 0.2:
        issues.append({
            "code": "missing_section_path",
            "severity": "high",
            "message": "超过 20% 的 section 没有章节路径，标题识别可能失效",
        })
    if margin_ratio > 0.4:
        issues.append({
            "code": "margin_removal",
            "severity": "medium",
            "message": "页眉页脚剔除比例异常，短文档可能误删正文",
        })
    return {"ok": not issues, "metrics": metrics, "issues": issues}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="检查 PDF 结构与 chunk 质量")
    parser.add_argument("structure", help="structure.json")
    parser.add_argument("chunks", help="chunks.jsonl")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="发现问题时返回退出码 1")
    args = parser.parse_args()

    structure_path = Path(args.structure)
    if not structure_path.exists():
        sys.exit(f"文件不存在: {structure_path}")
    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    chunks = pdf_tools.load_chunks(pdf_tools.resolve_index(args.chunks))
    report = inspect(structure, chunks)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        metrics = report["metrics"]
        print(
            f"pages={metrics['pages']} blocks={metrics['blocks']} "
            f"headings={metrics['headings']} chunks={metrics['chunks']}"
        )
        print(f"average_chunk_length={metrics['average_chunk_length']}")
        print(f"section_without_path_ratio={metrics['section_without_path_ratio']}")
        if report["issues"]:
            for issue in report["issues"]:
                print(f"- [{issue['severity']}] {issue['code']}: {issue['message']}")
        else:
            print("结构检查通过")
    if args.strict and report["issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
