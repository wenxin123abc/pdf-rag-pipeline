#!/usr/bin/env python3
"""L1 类型识别：判断 PDF 是文本型 / 扫描型 / 图文混排，并给出推荐解析管线。

用法:
    python detect_pdf_type.py <input.pdf> [--out type_report.json]

输出 JSON 字段:
    doc_type            text | scanned | mixed
    per_page[]          每页字符数、图片数、矢量图形数、图片覆盖率
    recommend{}         推荐解析器、是否需要 OCR、推荐索引策略
    warnings[]          多栏排版、加密、空白页等风险提示
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

TEXT_THRESHOLD = 50        # 单页可提取字符数低于此值视为"无文本层"
EMPTY_PAGE_RATIO_SCAN = 0.6   # 无文本页占比超过此值 -> 扫描件
EMPTY_PAGE_RATIO_MIXED = 0.12  # 超过此值但未达扫描件阈值 -> 图文混排
IMAGE_COVER_MIXED = 0.45   # 平均图片覆盖率超过此值 -> 图文混排


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_pymupdf():
    try:
        import pymupdf  # noqa: F401
        return pymupdf
    except ImportError:
        try:
            import fitz
            return fitz
        except ImportError:
            sys.exit(
                "缺少 PyMuPDF。请先安装：pip install pymupdf\n"
                "（Anaconda 环境通常已自带，可直接用其 python 解释器运行本脚本）"
            )


def open_pdf(fitz, path: Path, password: str):
    try:
        doc = fitz.open(path)
    except Exception as exc:
        sys.exit(f"无法打开 PDF: {exc}")
    if getattr(doc, "needs_pass", False):
        if not password or not doc.authenticate(password):
            doc.close()
            sys.exit("PDF 已加密，请通过 --password 提供正确密码")
    return doc


def analyze_page(page) -> dict:
    text = page.get_text("text")
    stripped = "".join(text.split())
    images = page.get_images(full=True)
    page_area = abs(page.rect.width * page.rect.height) or 1.0
    img_area = 0.0
    for item in images:
        xref = item[0]
        try:
            for rect in page.get_image_rects(xref):
                img_area += abs(rect.width * rect.height)
        except Exception:
            pass
    blocks = page.get_text("blocks")
    return {
        "page": page.number + 1,
        "chars": len(stripped),
        "text_blocks": len(blocks),
        "images": len(images),
        "drawings": len(page.get_drawings()),
        "image_coverage": round(min(img_area / page_area, 1.0), 3),
        "width": round(page.rect.width, 1),
        "height": round(page.rect.height, 1),
    }


def detect_columns(page) -> int:
    """根据正文块的横向覆盖带估计 1-4 栏。

    跨栏标题和通栏块不参与列带统计，避免一个标题把所有间隔填满。
    """
    blocks = [b for b in page.get_text("blocks") if b[4].strip()]
    if len(blocks) < 6:
        return 1
    width = page.rect.width or 1.0
    candidates = [b for b in blocks if (b[2] - b[0]) / width < 0.65]
    if len(candidates) < 6:
        return 1

    bins = 100
    occupied = [False] * bins
    for block in candidates:
        lo = max(0, min(bins - 1, int(block[0] / width * bins)))
        hi = max(0, min(bins - 1, int(block[2] / width * bins)))
        for idx in range(lo, hi + 1):
            occupied[idx] = True

    runs: list[tuple[int, int]] = []
    start = None
    for idx, used in enumerate(occupied + [False]):
        if used and start is None:
            start = idx
        elif not used and start is not None:
            runs.append((start, idx - 1))
            start = None

    groups = 0
    for lo, hi in runs:
        members = [
            b for b in candidates
            if lo <= ((b[0] + b[2]) / 2) / width * bins <= hi
        ]
        if len(members) >= 2:
            groups += 1
    return min(4, max(1, groups))


def classify(pages: list[dict]) -> tuple[str, dict]:
    total = len(pages) or 1
    empty_pages = [p for p in pages if p["chars"] < TEXT_THRESHOLD]
    avg_chars = sum(p["chars"] for p in pages) / total
    avg_cover = sum(p["image_coverage"] for p in pages) / total
    empty_ratio = len(empty_pages) / total

    if empty_ratio >= EMPTY_PAGE_RATIO_SCAN or avg_chars < 20:
        doc_type = "scanned"
    elif empty_ratio > EMPTY_PAGE_RATIO_MIXED or avg_cover > IMAGE_COVER_MIXED:
        doc_type = "mixed"
    else:
        doc_type = "text"

    stats = {
        "avg_chars_per_page": round(avg_chars, 1),
        "avg_image_coverage": round(avg_cover, 3),
        "empty_text_pages": len(empty_pages),
        "empty_text_ratio": round(empty_ratio, 3),
        "total_images": sum(p["images"] for p in pages),
    }
    return doc_type, stats


def recommend(doc_type: str, stats: dict, max_cols: int) -> dict:
    if doc_type == "text":
        return {
            "parser": "pymupdf",
            "alias": "PyMuPDF(fitz) 抽 text+blocks 保 bbox" if max_cols == 1
                     else "PyMuPDF 提取后按栏带排序；复杂版面可改用 pdfplumber",
            "ocr_needed": False,
            "index_strategy": "page-level embedding + 章节语义切片",
            "why": "存在完整文本层，无需 OCR；先按页定位再页内切片最省成本",
        }
    if doc_type == "scanned":
        return {
            "parser": "mineru_or_vlm",
            "alias": "MinerU / 任意多模态文档解析模型（逐页出 Markdown+版面）",
            "ocr_needed": True,
            "index_strategy": "页级视觉描述为主索引，识别文本作补充元数据",
            "why": "无文本层，纯 OCR 会丢掉图表与版面语义，必须走多模态解析",
        }
    return {
        "parser": "pymupdf+vlm_hybrid",
        "alias": "PyMuPDF 抽文本层 + 多模态模型补齐扫描页/图表语义",
        "ocr_needed": "per_page",
        "index_strategy": "区分图文主次：文字为主做全文切片，图表为主做页级视觉描述",
        "why": "部分页有文本层、部分页是图片，需按页决定走文本还是走视觉",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="判断 PDF 类型并推荐解析管线")
    ap.add_argument("pdf", help="输入 PDF 路径")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径，默认 <pdf>.type.json")
    ap.add_argument("--max-pages", type=int, default=0, help="仅抽样前 N 页（大文档提速）")
    ap.add_argument("--password", default="", help="加密 PDF 的密码")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    path = Path(args.pdf)
    if not path.exists():
        sys.exit(f"文件不存在: {path}")

    fitz = load_pymupdf()
    doc = open_pdf(fitz, path, args.password)
    page_count = len(doc)
    warnings: list[str] = []
    if not page_count:
        doc.close()
        sys.exit("PDF 没有可处理页面")
    if getattr(doc, "is_encrypted", False):
        warnings.append("文档已加密；本报告使用了已提供的密码读取内容")

    limit = page_count if args.max_pages <= 0 else min(args.max_pages, page_count)
    pages, col_counts = [], []
    for i in range(limit):
        page = doc[i]
        pages.append(analyze_page(page))
        col_counts.append(detect_columns(page))

    doc_type, stats = classify(pages)
    max_cols = max(col_counts) if col_counts else 1
    if max_cols >= 2:
        warnings.append(
            f"疑似 {max_cols} 栏排版：直接抽文本会把左右栏串行拼接，"
            "需按 x 坐标先切栏再排序"
        )
    if stats["empty_text_ratio"] > 0 and doc_type != "text":
        warnings.append(
            f"{stats['empty_text_pages']}/{limit} 页无可提取文本，"
            "这些页必须走 OCR 或多模态解析，否则整页丢失"
        )

    report = {
        "file": str(path.resolve()),
        "source_sha256": sha256_file(path),
        "pages_total": page_count,
        "pages_sampled": limit,
        "doc_type": doc_type,
        "stats": stats,
        "columns": {"max_detected": max_cols, "per_page": col_counts[:limit]},
        "recommend": recommend(doc_type, stats, max_cols),
        "per_page": pages,
        "warnings": warnings,
    }
    doc.close()

    out = Path(args.out) if args.out else path.with_suffix(".type.json")
    write_json_atomic(out, report)

    print(f"doc_type = {doc_type}  pages = {page_count}")
    print(f"推荐管线 = {report['recommend']['parser']} | OCR: {report['recommend']['ocr_needed']}")
    print(f"索引策略 = {report['recommend']['index_strategy']}")
    if warnings:
        print("风险提示:")
        for w in warnings:
            print(f"  - {w}")
    print(f"报告已写入 {out}")


if __name__ == "__main__":
    main()
