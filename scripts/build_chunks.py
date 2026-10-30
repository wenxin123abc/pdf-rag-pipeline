#!/usr/bin/env python3
"""L3 切片与索引：把结构 JSON 变成带 metadata 的 chunk，并补齐上下文前缀。

用法:
    python build_chunks.py <structure.json> [--out chunks.jsonl] [--max-chars 900]

设计要点（对应"切片与索引"层）：
    - 三类 chunk 分开：page（页级定位用）/ section（语义检索用）/ table / image
    - 每个 chunk 带完整 metadata：doc_id、页码、章节路径、表格编号、图片描述
    - text 保留原文（用于引用），embed_text 额外拼接上下文前缀（用于向量化）
    - 块边界切分不切断句子，避免重叠窗口带来的重复污染
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

SUMMARY_CHARS = 60      # 上下文前缀里"本段/上文/下文"摘要长度
IMAGE_GAP = 60.0        # 图片题注搜索半径（pt）
SCHEMA_VERSION = 2


def read_structure(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_sha256(data: dict, pdf_path: Path) -> str:
    recorded = data.get("source_sha256")
    if recorded:
        return str(recorded)
    if pdf_path.exists():
        return sha256_file(pdf_path)
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def doc_id_of(source_sha: str) -> str:
    return "pdf-" + source_sha[:16]


def quote_anchor(page: int, bbox: list[float] | None = None, **refs: str) -> str:
    values: dict[str, object] = {"page": page}
    values.update({key: value for key, value in refs.items() if value})
    if bbox and len(bbox) == 4:
        values["bbox"] = ",".join(f"{float(v):g}" for v in bbox)
    return urlencode(values, safe=",")


def guess_title(data: dict, pdf_path: Path) -> str:
    metadata_title = str((data.get("metadata") or {}).get("title") or "").strip()
    if metadata_title:
        return metadata_title[:120]
    for page in data["pages"]:
        for blk in page["blocks"]:
            if blk.get("is_heading"):
                return blk["text"].replace("\n", " ").strip()[:80]
    return pdf_path.stem


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def make_prefix(title: str, page: int, section: str, prev: str, nxt: str, self_sum: str) -> str:
    parts = [f"《{title}》第{page}页"]
    if section:
        parts.append(f"所在章节：{section}")
    if self_sum:
        parts.append(f"本片段讲：{self_sum}")
    if prev:
        parts.append(f"上文在讲：{prev}")
    if nxt:
        parts.append(f"下文将讲：{nxt}")
    return "｜".join(parts)


def find_caption(page: dict, img_bbox: list[float]) -> tuple[str, str]:
    """返回 (图片题注, 图内文字)。

    图内文字（图表标题、坐标轴标签、示意图里的说明）对检索极有价值——
    这些内容 OCR 抽不出来，只能靠读文本块或视觉模型。
    """
    best, best_d = "", IMAGE_GAP
    embedded = []
    for blk in page["blocks"]:
        text = squash(blk["text"])
        if not text or len(text) > 80:
            continue
        if blk.get("in_image"):
            embedded.append(text)
            continue
        y0, y1 = blk["bbox"][1], blk["bbox"][3]
        gap_below = y0 - img_bbox[3]
        gap_above = img_bbox[1] - y1
        gap = gap_below if 0 <= gap_below <= IMAGE_GAP else (
            gap_above if 0 <= gap_above <= IMAGE_GAP else 1e9)
        if gap < best_d:
            best, best_d = text, gap
    return best, " / ".join(embedded)


def split_long(text: str, max_chars: int) -> list[str]:
    """超长块按句子切，保证不把句子切断。"""
    if len(text) <= max_chars:
        return [text]
    sentences = re.split(r"(?<=[。！？；\n])", text)
    out, buf = [], ""
    for s in sentences:
        if not s:
            continue
        if len(buf) + len(s) > max_chars and buf:
            out.append(buf)
            buf = s
        else:
            buf += s
    if buf.strip():
        out.append(buf)
    # 极端情况（单句超长）硬切
    final = []
    for seg in out:
        while len(seg) > max_chars:
            final.append(seg[:max_chars])
            seg = seg[max_chars:]
        if seg.strip():
            final.append(seg)
    return final


def build(data: dict, pdf_path: Path, max_chars: int) -> tuple[list[dict], dict]:
    if max_chars <= 0:
        raise ValueError("max_chars 必须大于 0")
    pages = data.get("pages")
    if not isinstance(pages, list):
        raise ValueError("structure JSON 缺少 pages 数组")

    title = guess_title(data, pdf_path)
    source_sha = source_sha256(data, pdf_path)
    doc_id = doc_id_of(source_sha)
    chunks: list[dict] = []
    order = 0
    section_path: list[str] = []

    for page in pages:
        pno = int(page["page"])
        page_blocks = page.get("blocks") or []

        # 页级 chunk 保留整页文本，便于按页读取。
        page_text = "\n".join(b["text"] for b in page_blocks).strip()
        if page_text:
            order += 1
            chunks.append({
                "chunk_id": f"{doc_id}-p{pno}-page",
                "doc_id": doc_id,
                "doc_title": title,
                "source_sha256": source_sha,
                "page": pno,
                "chunk_type": "page",
                "order": order,
                "section_path": [],
                "char_len": len(page_text),
                "bbox": [0, 0, page["width"], page["height"]],
                "source_block_ids": [b.get("id", "") for b in page_blocks],
                "text": page_text,
                "context_prefix": f"《{title}》第{pno}页全文",
                "embed_text": f"《{title}》第{pno}页全文\n{page_text}",
                "quote_anchor": quote_anchor(pno),
            })

        buffer: list[dict] = []
        buffer_path: list[str] = []
        section_no = 0

        def flush(buf: list[dict], path: list[str]) -> None:
            nonlocal order, section_no
            if not buf:
                return
            raw = "\n".join(b["text"] for b in buf).strip()
            if not raw:
                return
            boxes = [b["bbox"] for b in buf if b.get("bbox")]
            merged_bbox = [
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            ] if boxes else [0, 0, page["width"], page["height"]]
            source_ids = [b.get("id", "") for b in buf]
            for part in split_long(raw, max_chars):
                order += 1
                section_no += 1
                chunks.append({
                    "chunk_id": f"{doc_id}-p{pno}-s{section_no}",
                    "doc_id": doc_id,
                    "doc_title": title,
                    "source_sha256": source_sha,
                    "page": pno,
                    "chunk_type": "section",
                    "order": order,
                    "section_path": list(path),
                    "char_len": len(part),
                    "bbox": merged_bbox,
                    "source_block_ids": source_ids,
                    "text": part,
                    "context_prefix": "",
                    "embed_text": "",
                    "quote_anchor": quote_anchor(pno, merged_bbox),
                })

        for blk in page_blocks:
            if blk.get("in_image"):
                continue
            embedded_path = blk.get("section_path")
            if isinstance(embedded_path, list):
                section_path = [str(item) for item in embedded_path]
            elif blk.get("is_heading"):
                level = max(1, int(blk.get("level") or 1))
                section_path = section_path[:max(0, level - 1)]
                section_path.append(squash(blk["text"])[:80])

            if blk.get("is_heading"):
                flush(buffer, buffer_path)
                buffer, buffer_path = [], []
                continue

            if buffer and buffer_path != section_path:
                flush(buffer, buffer_path)
                buffer, buffer_path = [], []
            if not buffer:
                buffer_path = list(section_path)
            buffer.append(blk)
            if sum(len(b["text"]) for b in buffer) >= max_chars:
                flush(buffer, buffer_path)
                buffer, buffer_path = [], []
        flush(buffer, buffer_path)

        def page_path_for_bbox(bbox: list[float]) -> list[str]:
            preceding = [
                blk for blk in page_blocks
                if blk.get("section_path") and blk.get("bbox")
                and blk["bbox"][1] < bbox[1]
            ]
            if preceding:
                return list(preceding[-1]["section_path"])
            return list(section_path)

        for table in page.get("tables") or []:
            order += 1
            caption = table.get("caption") or ""
            markdown = table["markdown"]
            text = f"{caption}\n{markdown}" if caption else markdown
            path = list(table.get("section_path") or page_path_for_bbox(table["bbox"]))
            chunks.append({
                "chunk_id": f"{doc_id}-{table['id']}",
                "doc_id": doc_id,
                "doc_title": title,
                "source_sha256": source_sha,
                "page": pno,
                "chunk_type": "table",
                "order": order,
                "section_path": path,
                "char_len": len(text),
                "bbox": table["bbox"],
                "source_block_ids": [table["id"]],
                "table_id": table["id"],
                "caption": caption,
                "rows": table["rows"],
                "cols": table["cols"],
                "text": text,
                "context_prefix": "",
                "embed_text": "",
                "quote_anchor": quote_anchor(pno, table["bbox"], table=table["id"]),
            })

        for image in page.get("images") or []:
            caption, embedded = find_caption(page, image["bbox"])
            description = str(image.get("visual_desc") or "").strip()
            order += 1
            parts = [f"[图片] {caption or '无题注'}"]
            if embedded:
                parts.append(f"图内文字：{embedded}")
            if description:
                parts.append(f"视觉描述：{description}")
            parts.append(f"第{pno}页 {image['width_px']}x{image['height_px']}px")
            text = "｜".join(parts)
            path = list(image.get("section_path") or page_path_for_bbox(image["bbox"]))
            chunks.append({
                "chunk_id": f"{doc_id}-{image['id']}",
                "doc_id": doc_id,
                "doc_title": title,
                "source_sha256": source_sha,
                "page": pno,
                "chunk_type": "image",
                "order": order,
                "section_path": path,
                "char_len": len(text),
                "bbox": image["bbox"],
                "source_block_ids": [image["id"]],
                "image_id": image["id"],
                "caption": caption,
                "embedded_text": embedded,
                "width_px": image["width_px"],
                "height_px": image["height_px"],
                "visual_desc": description or None,
                "needs_visual_desc": not bool(description),
                "text": text,
                "context_prefix": "",
                "embed_text": "",
                "quote_anchor": quote_anchor(pno, image["bbox"], image=image["id"]),
            })

    section_chunks = [c for c in chunks if c["chunk_type"] == "section"]
    for idx, chunk in enumerate(section_chunks):
        prev_text = squash(section_chunks[idx - 1]["text"])[:SUMMARY_CHARS] if idx else ""
        next_text = (
            squash(section_chunks[idx + 1]["text"])[:SUMMARY_CHARS]
            if idx + 1 < len(section_chunks) else ""
        )
        section = " > ".join(chunk["section_path"])
        self_summary = squash(chunk["text"])[:SUMMARY_CHARS]
        chunk["context_prefix"] = make_prefix(
            title, chunk["page"], section, prev_text, next_text, self_summary
        )
        chunk["embed_text"] = f"{chunk['context_prefix']}\n{chunk['text']}"

    for chunk in chunks:
        if chunk["chunk_type"] not in ("table", "image"):
            continue
        section = " > ".join(chunk["section_path"])
        kind = "表格" if chunk["chunk_type"] == "table" else "图片"
        chunk["context_prefix"] = (
            f"《{title}》第{chunk['page']}页 {kind}"
            + (f"｜章节：{section}" if section else "")
        )
        chunk["embed_text"] = f"{chunk['context_prefix']}\n{chunk['text']}"

    stats = {
        "schema_version": SCHEMA_VERSION,
        "doc_id": doc_id,
        "doc_title": title,
        "source_sha256": source_sha,
        "pages": len(pages),
        "chunks_total": len(chunks),
        "by_type": {
            kind: sum(1 for c in chunks if c["chunk_type"] == kind)
            for kind in ("page", "section", "table", "image")
        },
        "needs_visual_desc": sum(
            1 for c in chunks if c.get("needs_visual_desc")
        ),
        "avg_chars": round(
            sum(c["char_len"] for c in chunks) / max(len(chunks), 1), 1
        ),
    }
    return chunks, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="生成带 metadata 的 chunk 索引")
    ap.add_argument("structure", help="extract_structure.py 输出的 JSON")
    ap.add_argument("--pdf", default="", help="原始 PDF 路径（用于计算 doc_id），可省略")
    ap.add_argument("--out", default="", help="输出 jsonl 路径")
    ap.add_argument("--max-chars", type=int, default=900, help="单 chunk 最大字符数")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    sp = Path(args.structure)
    if not sp.exists():
        sys.exit(f"文件不存在: {sp}")
    data = read_structure(sp)
    pdf = Path(args.pdf) if args.pdf else Path(data["file"])

    try:
        chunks, stats = build(data, pdf, args.max_chars)
    except (KeyError, TypeError, ValueError) as exc:
        sys.exit(f"结构文件不兼容: {exc}")
    out = Path(args.out) if args.out else sp.with_name("chunks.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    chunks_tmp = out.with_name(out.name + ".tmp")
    with chunks_tmp.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    chunks_tmp.replace(out)

    meta = out.with_name("index_meta.json")
    meta_tmp = meta.with_name(meta.name + ".tmp")
    meta_tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_tmp.replace(meta)

    print(f"chunk 总数={stats['chunks_total']}  {stats['by_type']}")
    print(f"待补视觉描述={stats['needs_visual_desc']}  平均长度={stats['avg_chars']}")
    print(f"索引已写入 {out}")


if __name__ == "__main__":
    main()
