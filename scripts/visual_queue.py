#!/usr/bin/env python3
"""Crop PDF images and prepare model-independent visual-description prompts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pymupdf

import extract_structure
import pdf_tools


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_prompt(chunk: dict) -> str:
    context = []
    if chunk.get("caption"):
        context.append(f"题注：{chunk['caption']}")
    if chunk.get("embedded_text"):
        context.append(f"图内文字：{chunk['embedded_text']}")
    extra = "\n".join(context)
    return (
        "请只根据图像中可见的信息生成适合检索的中文描述。"
        "说明图表类型、对象、趋势、关键数值和单位；看不清的内容明确写“无法确认”，"
        "不要补充图中没有的事实。\n"
        f"页码：{chunk.get('page')}\n"
        f"{extra}"
    ).strip()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="生成 PDF 图片视觉描述队列")
    parser.add_argument("chunks", help="chunks.jsonl")
    parser.add_argument("--pdf", required=True, help="原始 PDF")
    parser.add_argument("--out", required=True, help="队列输出目录")
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    chunks_path = pdf_tools.resolve_index(args.chunks)
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"PDF 不存在: {pdf_path}")
    chunks = [
        chunk for chunk in pdf_tools.load_chunks(chunks_path)
        if chunk.get("chunk_type") == "image"
        and chunk.get("needs_visual_desc")
        and not chunk.get("visual_desc")
    ]

    output = Path(args.out)
    image_dir = output / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    fitz = extract_structure.load_pymupdf()
    doc = extract_structure.open_pdf(fitz, pdf_path, args.password)

    prompts = []
    descriptions = {}
    try:
        for chunk in chunks:
            page_number = int(chunk["page"])
            if not 1 <= page_number <= len(doc):
                continue
            page = doc[page_number - 1]
            clip = fitz.Rect(chunk["bbox"])
            pixmap = page.get_pixmap(
                clip=clip,
                matrix=fitz.Matrix(2, 2),
                alpha=False,
            )
            png = pixmap.tobytes("png")
            digest = hashlib.sha256(png).hexdigest()
            relative_path = f"images/{digest[:20]}.png"
            (output / relative_path).write_bytes(png)
            prompts.append({
                "image_id": chunk["image_id"],
                "chunk_id": chunk["chunk_id"],
                "page": page_number,
                "bbox": chunk["bbox"],
                "png_path": relative_path,
                "caption": chunk.get("caption", ""),
                "embedded_text": chunk.get("embedded_text", ""),
                "prompt": build_prompt(chunk),
            })
            descriptions[chunk["image_id"]] = ""
    finally:
        doc.close()

    write_json(output / "prompts.json", prompts)
    write_json(output / "descriptions.template.json", descriptions)
    write_json(
        output / "queue_meta.json",
        {
            "schema_version": 1,
            "source_pdf": str(pdf_path.resolve()),
            "chunks": str(chunks_path.resolve()),
            "images": len(prompts),
        },
    )
    print(f"视觉队列已生成: {len(prompts)} 张图片")
    print(f"提示词: {output / 'prompts.json'}")
    print(f"描述模板: {output / 'descriptions.template.json'}")


if __name__ == "__main__":
    main()
