#!/usr/bin/env python3
"""L2 结构还原：抽取每页的文本块、标题层级、表格、图片，并剔除页眉页脚。

用法:
    python extract_structure.py <input.pdf> [--out structure.json] [--columns N]

要点（对应"结构还原"层）：
    - 每个块保留 bbox、字号、是否加粗，用于判断标题层级
    - 跨页统计重复出现的顶/底部文本 -> 标记为 header/footer 并从正文剔除
    - 表格单独还原成 markdown，不与正文混在一起
    - 图片记录 bbox 与像素尺寸，供后续生成视觉描述索引
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

HEADER_BAND = 0.085   # 页面上/下 8.5% 视为页眉页脚候选带
FOOTER_BAND = 0.915
REPEAT_RATIO = 0.5    # 同一文本出现在 >=50% 页面的页眉/页脚带 -> 判定为页眉页脚

HEADING_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百零〇\d]+[章节条编部分]"),
    re.compile(r"^[一二三四五六七八九十]+[、.]"),
    re.compile(r"^\d+(\.\d+){0,3}[\s、.]+\S"),
    re.compile(r"^(摘\s*要|前\s*言|引\s*言|目\s*录|参考文献|附\s*录|结\s*论)"),
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
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
            sys.exit("缺少 PyMuPDF。请先安装：pip install pymupdf")


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


def raw_blocks(page) -> list[dict]:
    """把一个页面拆成块级结构，保留字号与加粗信息。"""
    out = []
    data = page.get_text("dict")
    for blk in data.get("blocks", []):
        if blk.get("type") != 0:      # 0 = 文本块
            continue
        texts, sizes, bold_chars, total_chars = [], [], 0, 0
        for line in blk.get("lines", []):
            line_text = ""
            for span in line.get("spans", []):
                t = span.get("text", "")
                line_text += t
                n = len(t.strip())
                if n:
                    total_chars += n
                    sizes.append(round(span.get("size", 0), 1))
                    font = (span.get("font") or "").lower()
                    if (span.get("flags", 0) & 16) or "bold" in font or "black" in font:
                        bold_chars += n
            if line_text.strip():
                texts.append(line_text.strip())
        text = "\n".join(texts).replace("\xa0", " ").strip()
        if not text:
            continue
        bbox = [round(v, 1) for v in blk.get("bbox", (0, 0, 0, 0))]
        out.append({
            "bbox": bbox,
            "text": text,
            "size": round(max(sizes), 1) if sizes else 0.0,
            "bold_ratio": round(bold_chars / total_chars, 2) if total_chars else 0.0,
        })
    return out


def rebuild_paragraphs(blocks: list[dict]) -> list[dict]:
    """把被拆成多行的段落接回整段，并让孤立编号行归属下一段。

    PDF 抽文本常把一段话拆成"每行一块"，或用独立文本框放编号（如"3.1"）。
    不重建的话，切片会出现半句话，检索回来模型读不懂——这正是 PDF-RAG 的主要坑。
    """
    out: list[dict] = []
    for b in blocks:
        cur = dict(b)
        cur["text"] = cur["text"].strip()
        if not cur["text"]:
            continue
        if out:
            prev = out[-1]
            gap = cur["bbox"][1] - prev["bbox"][3]
            lh = max(prev["bbox"][3] - prev["bbox"][1], 1.0)
            prev_text = prev["text"].rstrip()
            multiline = "\n" in prev_text or "\n" in cur["text"]
            same_margin = abs(cur["bbox"][0] - prev["bbox"][0]) < 3
            # 字号不同 = 结构边界（标题/题注/脚注），绝不合并
            same_size = abs(prev["size"] - cur["size"]) <= 0.6
            dangling = not prev_text.endswith(
                ("。", "！", "？", "；", "：", ".", ";", ":", "|"))
            if (not multiline) and same_margin and same_size and dangling and gap <= 0.85 * lh:
                sep = " " if re.search(r"[0-9A-Za-z]$", prev_text) else ""
                prev["text"] = prev_text + sep + cur["text"]
                prev["bbox"] = [prev["bbox"][0], prev["bbox"][1],
                                max(prev["bbox"][2], cur["bbox"][2]), cur["bbox"][3]]
                prev["size"] = max(prev["size"], cur["size"])
                prev["bold_ratio"] = min(prev["bold_ratio"], cur["bold_ratio"])
                continue
        out.append(cur)
    return out


def merge_line_blocks(blocks: list[dict], page_width: float) -> list[dict]:
    """合并同一行被 PDF 拆碎的块（如编号"1.1"与正文被分成两块）。

    真实文档里很常见：编号、加粗前缀、公式往往单独成块。不合并会导致
    标题误判、语义切片把一句话切成两半。
    """
    if len(blocks) < 2:
        return blocks
    lines: list[dict] = []
    for b in sorted(blocks, key=lambda x: ((x["bbox"][1] + x["bbox"][3]) / 2, x["bbox"][0])):
        cy = (b["bbox"][1] + b["bbox"][3]) / 2
        h = b["bbox"][3] - b["bbox"][1]
        target = None
        for ln in lines:
            if abs(cy - ln["cy"]) <= 0.45 * max(h, ln["h"], 1):
                target = ln
                break
        if target is None:
            lines.append({"cy": cy, "h": h, "items": [b]})
        else:
            target["items"].append(b)
            target["cy"] = (target["cy"] + cy) / 2
            target["h"] = max(target["h"], h)

    out: list[dict] = []
    for ln in lines:
        items = sorted(ln["items"], key=lambda x: x["bbox"][0])
        cur = dict(items[0])
        for b in items[1:]:
            gap = b["bbox"][0] - cur["bbox"][2]
            size = max(cur["size"], b["size"], 1.0)
            too_far = gap > 2.5 * size
            too_wide = (b["bbox"][2] - cur["bbox"][0]) > page_width * 0.95
            if too_far or too_wide:
                out.append(cur)
                cur = dict(b)
                continue
            sep = " " if gap > 0.25 * size else ""
            total = len(cur["text"]) + len(b["text"]) or 1
            cur = {
                "bbox": [min(cur["bbox"][0], b["bbox"][0]), min(cur["bbox"][1], b["bbox"][1]),
                         max(cur["bbox"][2], b["bbox"][2]), max(cur["bbox"][3], b["bbox"][3])],
                "text": cur["text"] + sep + b["text"],
                "size": max(cur["size"], b["size"]),
                "bold_ratio": round((cur["bold_ratio"] * len(cur["text"])
                                     + b["bold_ratio"] * len(b["text"])) / total, 2),
            }
        out.append(cur)
    return out


def detect_repeat_margins(all_blocks: list[list[dict]], heights: list[float]) -> set[str]:
    """跨页统计页眉/页脚带里重复出现的文本。"""
    counter = Counter()
    for blocks, h in zip(all_blocks, heights):
        seen = set()
        for b in blocks:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            if h <= 0:
                continue
            if y1 < h * HEADER_BAND or y0 > h * FOOTER_BAND:
                key = re.sub(r"\d+", "#", b["text"])[:80]
                if key not in seen:
                    counter[key] += 1
                    seen.add(key)
    need = max(2, int(len(all_blocks) * REPEAT_RATIO))
    return {k for k, v in counter.items() if v >= need}


def order_blocks(blocks: list[dict], width: float, columns: int) -> list[dict]:
    """多栏排版下按栏再按 y 排序，避免左右栏串行。"""
    if columns <= 1 or width <= 0:
        return sorted(blocks, key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
    ncols = max(2, min(columns, 4))

    def column_of(block: dict) -> int:
        center = (block["bbox"][0] + block["bbox"][2]) / 2
        return max(0, min(ncols - 1, int(center / width * ncols)))

    return sorted(
        blocks,
        key=lambda b: (column_of(b), round(b["bbox"][1], 1), b["bbox"][0]),
    )


def guess_heading(body_size: float, blk: dict) -> tuple[bool, int | None]:
    text = blk["text"].replace("\n", " ").strip()
    if not text or len(text) > 60:
        return False, None
    # 含句号的是完整句子，几乎不可能是标题，避免"3.1 乙方应……。"被误判
    looks_like_sentence = text.endswith(("。", "；", "：", "，", ".", ";")) or "。" in text[:46]
    size_hit = body_size > 0 and blk["size"] >= body_size * 1.12
    style_hit = blk["bold_ratio"] >= 0.6 and blk["size"] >= body_size * 0.98
    pattern_hit = any(p.match(text) for p in HEADING_PATTERNS) and len(text) <= 40

    if size_hit and not looks_like_sentence:
        ok = True
    elif pattern_hit and not looks_like_sentence:
        ok = True
    elif style_hit and len(text) <= 30 and not looks_like_sentence:
        ok = True
    else:
        ok = False
    if not ok:
        return False, None
    if body_size <= 0:
        return True, 1
    ratio = blk["size"] / body_size
    level = 1 if ratio >= 1.45 else 2 if ratio >= 1.22 else 3
    return True, level


def table_to_markdown(rows: list[list]) -> str:
    clean = []
    for row in rows:
        cells = [
            ("" if c is None else str(c).replace("\n", " ").replace("|", r"\|").strip())
            for c in row
        ]
        if any(cells):
            clean.append(cells)
    if not clean:
        return ""
    width = max(len(r) for r in clean)
    clean = [r + [""] * (width - len(r)) for r in clean]
    head, body = clean[0], clean[1:]
    lines = ["| " + " | ".join(head) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def extract(path: Path, columns: int, password: str = "") -> dict:
    fitz = load_pymupdf()
    doc = open_pdf(fitz, path, password)

    all_blocks, heights = [], []
    for page in doc:
        merged = merge_line_blocks(raw_blocks(page), page.rect.width)
        all_blocks.append(rebuild_paragraphs(merged))
        heights.append(page.rect.height)

    repeats = detect_repeat_margins(all_blocks, heights)

    # 正文字号取全文档众数，保证跨页标题层级判定一致
    size_weight: Counter = Counter()
    for blocks in all_blocks:
        for b in blocks:
            if b["text"]:
                size_weight[b["size"]] += len(b["text"])
    doc_body_size = size_weight.most_common(1)[0][0] if size_weight else 0.0

    pages_out = []
    section_stack: list[tuple[int, str]] = []
    for page, blocks, h in zip(doc, all_blocks, heights):
        for b in blocks:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            in_margin = h > 0 and (y1 < h * HEADER_BAND or y0 > h * FOOTER_BAND)
            key = re.sub(r"\d+", "#", b["text"])[:80]
            b["role"] = "margin" if (in_margin and key in repeats) else "body"

        body_blocks = [b for b in blocks if b["role"] == "body"] or blocks

        tables, images = [], []
        try:
            for t_idx, table in enumerate(page.find_tables().tables):
                md = table_to_markdown(table.extract())
                if not md:
                    continue
                tables.append({
                    "id": f"p{page.number + 1}-t{t_idx}",
                    "bbox": [round(v, 1) for v in table.bbox],
                    "rows": table.row_count,
                    "cols": table.col_count,
                    "markdown": md,
                    "caption": "",
                })
        except Exception:
            pass

        # 表格区域内的文本块已由表格结构化承载，从正文剔除，避免重复与乱序
        def in_table(b: dict) -> bool:
            cx = (b["bbox"][0] + b["bbox"][2]) / 2
            cy = (b["bbox"][1] + b["bbox"][3]) / 2
            return any(t["bbox"][0] <= cx <= t["bbox"][2] and t["bbox"][1] <= cy <= t["bbox"][3]
                       for t in tables)

        kept = [b for b in body_blocks if not in_table(b)]
        body_size = doc_body_size

        # 表格题注：表格上方或下方 40pt 内以“表/Table”开头的块
        for t in tables:
            best, best_gap = "", 41.0
            for b in kept:
                text = b["text"].replace("\n", " ").strip()
                if not text.startswith(("表", "Table", "TABLE")):
                    continue
                gap_below = b["bbox"][1] - t["bbox"][3]
                gap_above = t["bbox"][1] - b["bbox"][3]
                gap = min(
                    gap_below if gap_below >= 0 else float("inf"),
                    gap_above if gap_above >= 0 else float("inf"),
                )
                if gap < best_gap:
                    best, best_gap = text[:100], gap
            t["caption"] = best

        caption_texts = {t["caption"] for t in tables if t["caption"]}
        kept = [b for b in kept
                if b["text"].replace("\n", " ").strip() not in caption_texts]

        ordered = order_blocks(kept, page.rect.width, columns)

        for i_idx, item in enumerate(page.get_images(full=True)):
            xref = item[0]
            rects = []
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                pass
            for rect_idx, r in enumerate(rects):
                if abs(r.width * r.height) < 400:   # 过滤装饰性小图
                    continue
                rect_suffix = f"-r{rect_idx}" if len(rects) > 1 else ""
                images.append({
                    "id": f"p{page.number + 1}-i{i_idx}{rect_suffix}",
                    "bbox": [round(v, 1) for v in r],
                    "width_px": item[2],
                    "height_px": item[3],
                })

        def in_image(b: dict) -> bool:
            cx = (b["bbox"][0] + b["bbox"][2]) / 2
            cy = (b["bbox"][1] + b["bbox"][3]) / 2
            return any(i["bbox"][0] <= cx <= i["bbox"][2] and i["bbox"][1] <= cy <= i["bbox"][3]
                       for i in images)

        for idx, b in enumerate(ordered):
            b["id"] = f"p{page.number + 1}-b{idx}"
            b["in_image"] = in_image(b)
            is_h, level = guess_heading(body_size, b)
            # 图片内部的文字（图表标题、坐标轴标签）不构成章节标题
            if b["in_image"]:
                is_h, level = False, None
            b["is_heading"] = is_h
            b["level"] = level
            if is_h:
                section_stack = [
                    (lv, text) for lv, text in section_stack if lv < level
                ]
                section_stack.append((level, b["text"].replace("\n", " ").strip()[:80]))
            b["section_path"] = [text for _, text in section_stack]

        def section_for_bbox(bbox: list[float]) -> list[str]:
            before = [
                b for b in ordered
                if b["bbox"][3] <= bbox[1] + 8 or b["bbox"][1] < bbox[1]
            ]
            return list(before[-1].get("section_path", [])) if before else []

        for table in tables:
            table["section_path"] = section_for_bbox(table["bbox"])
        for image in images:
            image["section_path"] = section_for_bbox(image["bbox"])

        pages_out.append({
            "page": page.number + 1,
            "width": round(page.rect.width, 1),
            "height": round(page.rect.height, 1),
            "body_font_size": body_size,
            "blocks": ordered,
            "dropped_margin_blocks": len([b for b in blocks if b["role"] == "margin"]),
            "tables": tables,
            "images": images,
        })

    total_pages = len(doc)
    metadata = dict(doc.metadata or {})
    doc.close()
    return {
        "schema_version": 2,
        "file": str(path.resolve()),
        "source_sha256": sha256_file(path),
        "extractor": "pymupdf",
        "metadata": metadata,
        "columns": columns,
        "pages_total": total_pages,
        "pages": pages_out,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="抽取 PDF 页面结构（块/标题/表格/图片）")
    ap.add_argument("pdf")
    ap.add_argument("--out", default="")
    ap.add_argument("--columns", type=int, default=1, help="栏数 1-4；多栏文档按 detect 报告设置")
    ap.add_argument("--password", default="", help="加密 PDF 的密码")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    path = Path(args.pdf)
    if not path.exists():
        sys.exit(f"文件不存在: {path}")
    if not 1 <= args.columns <= 4:
        sys.exit("--columns 必须在 1 到 4 之间")

    data = extract(path, args.columns, args.password)
    if not data["pages_total"]:
        sys.exit("PDF 没有可处理页面")
    out = Path(args.out) if args.out else path.with_suffix(".structure.json")
    write_json_atomic(out, data)

    n_blocks = sum(len(p["blocks"]) for p in data["pages"])
    n_head = sum(len([b for b in p["blocks"] if b["is_heading"]]) for p in data["pages"])
    n_tab = sum(len(p["tables"]) for p in data["pages"])
    n_img = sum(len(p["images"]) for p in data["pages"])
    n_drop = sum(p["dropped_margin_blocks"] for p in data["pages"])
    print(f"页数={data['pages_total']} 块={n_blocks} 标题={n_head} 表格={n_tab} 图片={n_img} 剔除页眉页脚块={n_drop}")
    print(f"结构已写入 {out}")


if __name__ == "__main__":
    main()
