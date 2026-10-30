#!/usr/bin/env python3
"""Apply externally generated visual descriptions to image chunks."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def load_chunks(path: Path) -> list[dict]:
    chunks = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                chunks.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} 不是有效 JSON: {exc}") from exc
    return chunks


def load_descriptions(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = [
            (item.get("image_id") or item.get("chunk_id"), item.get("description"))
            for item in raw
        ]
    else:
        raise ValueError("描述文件必须是 {id: description} 对象或对象数组")

    result = {}
    for key, value in items:
        if not key or not isinstance(value, str) or not value.strip():
            raise ValueError("每条描述都必须包含非空 id 和 description")
        result[str(key)] = value.strip()
    return result


def resolve_image(chunks: list[dict], selector: str) -> dict:
    images = [c for c in chunks if c.get("chunk_type") == "image"]
    exact = [
        c for c in images
        if selector in {c.get("chunk_id"), c.get("image_id")}
    ]
    if len(exact) == 1:
        return exact[0]
    suffix = [
        c for c in images
        if str(c.get("chunk_id", "")).endswith("-" + selector)
        or str(c.get("image_id", "")).endswith("-" + selector)
    ]
    if len(suffix) == 1:
        return suffix[0]
    if not suffix:
        raise ValueError(f"找不到图片: {selector}")
    raise ValueError(f"图片选择器不唯一: {selector}")


def rebuild_text(chunk: dict, description: str) -> str:
    parts = [f"[图片] {chunk.get('caption') or '无题注'}"]
    if chunk.get("embedded_text"):
        parts.append(f"图内文字：{chunk['embedded_text']}")
    parts.append(f"视觉描述：{description}")
    width = chunk.get("width_px")
    height = chunk.get("height_px")
    if width and height:
        parts.append(f"第{chunk['page']}页 {width}x{height}px")
    return "｜".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="把视觉模型生成的描述合并到 PDF RAG 索引")
    ap.add_argument("chunks", help="chunks.jsonl 路径")
    ap.add_argument("descriptions", help="图片描述 JSON 路径")
    ap.add_argument("--out", default="", help="输出路径，默认原子更新输入文件")
    ap.add_argument("--dry-run", action="store_true", help="只校验匹配，不写文件")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    chunks_path = Path(args.chunks)
    descriptions_path = Path(args.descriptions)
    if not chunks_path.exists():
        sys.exit(f"文件不存在: {chunks_path}")
    if not descriptions_path.exists():
        sys.exit(f"文件不存在: {descriptions_path}")

    try:
        chunks = load_chunks(chunks_path)
        descriptions = load_descriptions(descriptions_path)
        targets = {
            selector: resolve_image(chunks, selector)
            for selector in descriptions
        }
    except (ValueError, KeyError, TypeError) as exc:
        sys.exit(f"描述合并失败: {exc}")

    for selector, chunk in targets.items():
        description = descriptions[selector]
        chunk["visual_desc"] = description
        chunk["needs_visual_desc"] = False
        chunk["text"] = rebuild_text(chunk, description)
        chunk["char_len"] = len(chunk["text"])
        chunk["embed_text"] = f"{chunk.get('context_prefix', '')}\n{chunk['text']}".lstrip()

    if args.dry_run:
        print(f"匹配成功: {len(targets)} 张图片（未写入）")
        return

    out = Path(args.out) if args.out else chunks_path
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    os.replace(tmp, out)
    remaining = sum(1 for c in chunks if c.get("needs_visual_desc"))
    meta_path = out.with_name("index_meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["needs_visual_desc"] = remaining
            meta_tmp = meta_path.with_name(meta_path.name + ".tmp")
            meta_tmp.write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(meta_tmp, meta_path)
        except (OSError, json.JSONDecodeError):
            print(f"警告: 无法更新 {meta_path}", file=sys.stderr)
    print(f"已更新图片描述: {len(targets)}  仍待补充: {remaining}")
    print(f"索引已写入 {out}")


if __name__ == "__main__":
    main()
