#!/usr/bin/env python3
"""Inspect and search a PDF RAG chunk index without third-party dependencies."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

K1, B = 1.5, 0.75
CJK = r"\u3400-\u4dbf\u4e00-\u9fff"
TOKEN_RE = re.compile(rf"[A-Za-z0-9_]+|[{CJK}]+")
REQUIRED_FIELDS = (
    "chunk_id", "doc_id", "doc_title", "page", "chunk_type",
    "section_path", "text", "embed_text", "quote_anchor",
)


def resolve_index(value: str) -> Path:
    path = Path(value)
    if path.is_dir():
        path = path / "chunks.jsonl"
    if not path.exists():
        sys.exit(f"找不到索引文件: {path}")
    return path


def load_chunks(path: Path) -> list[dict]:
    chunks = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.exit(f"{path}:{line_no} 不是有效 JSON: {exc}")
            if not isinstance(item, dict):
                sys.exit(f"{path}:{line_no} 必须是 JSON 对象")
            chunks.append(item)
    if not chunks:
        sys.exit(f"索引为空: {path}")
    return chunks


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group()
        if token[0].isascii():
            tokens.append(token.lower())
            continue
        tokens.extend(token)
        tokens.extend(token[i:i + 2] for i in range(len(token) - 1))
    return tokens


def bm25_scores(query: str, chunks: list[dict]) -> list[float]:
    query_terms = set(tokenize(query))
    if not query_terms:
        return [0.0] * len(chunks)
    docs = [tokenize(c.get("embed_text") or c.get("text", "")) for c in chunks]
    avg_len = sum(len(doc) for doc in docs) / max(len(docs), 1)
    document_frequency = Counter()
    for doc in docs:
        document_frequency.update(set(doc))
    total = len(docs)
    scores = []
    for doc in docs:
        frequencies = Counter(doc)
        length = len(doc) or 1
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            inverse_frequency = math.log(
                1 + (total - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            score += inverse_frequency * (
                frequency * (K1 + 1)
                / (frequency + K1 * (1 - B + B * length / avg_len))
            )
        scores.append(score)
    return scores


def flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def snippet(text: str, query: str, width: int = 90) -> str:
    flat = flatten(text)
    terms = sorted(
        (term for term in set(tokenize(query)) if len(term) >= 2),
        key=lambda term: (-len(term), term),
    )
    position = next((flat.find(term) for term in terms if flat.find(term) >= 0), -1)
    if position < 0:
        return flat[:width * 2] + ("…" if len(flat) > width * 2 else "")
    start = max(0, position - width // 2)
    end = min(len(flat), start + width * 2)
    return (
        ("…" if start > 0 else "")
        + flat[start:end]
        + ("…" if end < len(flat) else "")
    )


def search_hits(
    chunks: list[dict], query: str, limit: int, min_score: float = 0.0
) -> list[tuple[float, dict]]:
    scores = bm25_scores(query, chunks)
    ranked = sorted(
        zip(scores, chunks),
        key=lambda pair: (-pair[0], pair[1].get("page", 0), pair[1].get("order", 0)),
    )
    return [(score, chunk) for score, chunk in ranked if score > min_score][:limit]


def result_record(score: float, chunk: dict, query: str) -> dict:
    return {
        "score": round(score, 4),
        "chunk_id": chunk["chunk_id"],
        "page": chunk["page"],
        "type": chunk["chunk_type"],
        "section": " > ".join(chunk.get("section_path") or []),
        "quote_anchor": chunk.get("quote_anchor", ""),
        "snippet": snippet(chunk["text"], query),
    }


def cmd_search(args) -> None:
    if args.k <= 0:
        sys.exit("-k 必须大于 0")
    chunks = load_chunks(resolve_index(args.index))
    if args.type:
        allowed = {part.strip() for part in args.type.split(",") if part.strip()}
        chunks = [c for c in chunks if c.get("chunk_type") in allowed]
    if not chunks:
        sys.exit("没有可检索的 chunk（检查 --type）")

    hits = search_hits(chunks, args.query, args.k, args.min_score)
    if args.json:
        print(json.dumps(
            [result_record(score, chunk, args.query) for score, chunk in hits],
            ensure_ascii=False,
            indent=2,
        ))
        return
    if not hits:
        print("未命中：当前索引中未找到相关片段。")
        return
    for index, (score, chunk) in enumerate(hits, 1):
        record = result_record(score, chunk, args.query)
        heading = (
            f"[{index}] p{chunk['page']} · {chunk['chunk_type']} "
            f"· score={score:.2f}"
        )
        if record["section"]:
            heading += f" · {record['section']}"
        print(heading)
        print(f"    id: {chunk['chunk_id']}")
        print(f"    anchor: {chunk.get('quote_anchor', '')}")
        print(f"    {record['snippet']}")


def chunk_matches(chunk: dict, selector: str, chunk_type: str | None = None) -> bool:
    if chunk_type and chunk.get("chunk_type") != chunk_type:
        return False
    candidates = {
        str(chunk.get("chunk_id", "")),
        str(chunk.get("table_id", "")),
        str(chunk.get("image_id", "")),
    }
    if selector in candidates:
        return True
    return any(value.endswith("-" + selector) for value in candidates if value)


def resolve_chunks(
    chunks: list[dict], selector: str, chunk_type: str | None = None
) -> list[dict]:
    exact = [
        c for c in chunks
        if selector in {c.get("chunk_id"), c.get("table_id"), c.get("image_id")}
        and (not chunk_type or c.get("chunk_type") == chunk_type)
    ]
    matches = exact or [
        c for c in chunks if chunk_matches(c, selector, chunk_type)
    ]
    if not matches:
        sys.exit(f"未找到 chunk: {selector}")
    if len(matches) > 1:
        ids = ", ".join(c["chunk_id"] for c in matches[:8])
        sys.exit(f"选择器不唯一: {selector}；候选: {ids}")
    return matches


def render_chunk(chunk: dict) -> str:
    section = " > ".join(chunk.get("section_path") or [])
    lines = [
        f"# {chunk.get('doc_title', '')} 第 {chunk['page']} 页 · {chunk['chunk_type']}",
        f"# id: {chunk['chunk_id']}",
        f"# anchor: {chunk.get('quote_anchor', '')}",
    ]
    if section:
        lines.append(f"# section: {section}")
    if chunk.get("context_prefix"):
        lines.append(f"# context: {chunk['context_prefix']}")
    if chunk.get("visual_desc"):
        lines.append(f"# visual description: {chunk['visual_desc']}")
    return "\n".join(lines) + "\n\n" + chunk["text"]


def cmd_read(args) -> None:
    chunks = load_chunks(resolve_index(args.index))
    pages = sorted(
        (c for c in chunks if c.get("chunk_type") == "page" and c.get("page") == args.page),
        key=lambda c: c.get("order", 0),
    )
    if not pages:
        available = sorted({c["page"] for c in chunks if c.get("page")})
        sys.exit(f"第 {args.page} 页无可提取文本；有内容的页: {available[:30]}")
    print(render_chunk(pages[0]))


def cmd_items(args, chunk_type: str) -> None:
    chunks = load_chunks(resolve_index(args.index))
    if args.id:
        matches = resolve_chunks(chunks, args.id, chunk_type)
    elif args.page is not None:
        matches = [
            c for c in chunks
            if c.get("chunk_type") == chunk_type and c.get("page") == args.page
        ]
    else:
        sys.exit("请提供 --id 或 --page")
    if not matches:
        label = "表格" if chunk_type == "table" else "图片"
        sys.exit(f"第 {args.page} 页没有可用{label} chunk")
    if args.json:
        print(json.dumps(matches, ensure_ascii=False, indent=2))
        return
    for index, chunk in enumerate(matches):
        if index:
            print()
        print(render_chunk(chunk))


def cmd_quote(args) -> None:
    chunks = load_chunks(resolve_index(args.index))
    matches = resolve_chunks(chunks, args.chunk_id)
    if args.json:
        print(json.dumps(matches[0], ensure_ascii=False, indent=2))
        return
    print(render_chunk(matches[0]))


def cmd_stats(args) -> None:
    chunks = load_chunks(resolve_index(args.index))
    by_type = Counter(c.get("chunk_type", "") for c in chunks)
    pages = sorted({c.get("page") for c in chunks if isinstance(c.get("page"), int)})
    ids = Counter(c.get("chunk_id") for c in chunks)
    duplicates = sorted(str(key) for key, count in ids.items() if count > 1)
    missing = {
        c.get("chunk_id", f"row-{index}"): [
            field for field in REQUIRED_FIELDS if field not in c
        ]
        for index, c in enumerate(chunks)
    }
    missing = {key: fields for key, fields in missing.items() if fields}
    visual_pending = [
        c for c in chunks
        if c.get("chunk_type") == "image"
        and c.get("needs_visual_desc")
        and not c.get("visual_desc")
    ]
    result = {
        "chunks": len(chunks),
        "by_type": dict(by_type),
        "pages_covered": len(pages),
        "page_range": [pages[0], pages[-1]] if pages else None,
        "average_chars": round(
            sum(c.get("char_len", len(c.get("text", ""))) for c in chunks)
            / len(chunks),
            1,
        ),
        "visual_descriptions_pending": len(visual_pending),
        "duplicate_chunk_ids": duplicates,
        "missing_required_fields": missing,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"chunks = {len(chunks)}  {dict(by_type)}")
        page_range = f"{pages[0]}~{pages[-1]}" if pages else "-"
        print(f"覆盖页数 = {len(pages)}  页码范围 = {page_range}")
        print(f"待补视觉描述 = {len(visual_pending)}")
        print(f"平均 chunk 长度 = {result['average_chars']:.1f} 字符")
        print(f"重复 chunk_id = {len(duplicates)}")
        print(f"缺字段 chunk = {len(missing)}")
        if visual_pending:
            print("下一步：生成视觉描述后用 apply_visual_descriptions.py 回填")
    if duplicates or missing:
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF RAG 索引检索与质检工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    search = sub.add_parser("search", help="BM25 关键词/中文 n-gram 检索")
    search.add_argument("index")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5)
    search.add_argument("--type", default="", help="如 page,section,table,image")
    search.add_argument("--min-score", type=float, default=0.0)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=cmd_search)

    read = sub.add_parser("read", help="读取指定 PDF 页")
    read.add_argument("index")
    read.add_argument("--page", type=int, required=True)
    read.set_defaults(func=cmd_read)

    table = sub.add_parser("table", help="按表格 id 或页码读取结构化表格")
    table.add_argument("index")
    table.add_argument("--id", default="")
    table.add_argument("--page", type=int)
    table.add_argument("--json", action="store_true")
    table.set_defaults(func=lambda args: cmd_items(args, "table"))

    image = sub.add_parser("image", help="按图片 id 或页码读取图片 chunk")
    image.add_argument("index")
    image.add_argument("--id", default="")
    image.add_argument("--page", type=int)
    image.add_argument("--json", action="store_true")
    image.set_defaults(func=lambda args: cmd_items(args, "image"))

    quote = sub.add_parser("quote", help="按完整 id 或唯一后缀读取原文")
    quote.add_argument("index")
    quote.add_argument("--chunk-id", required=True)
    quote.add_argument("--json", action="store_true")
    quote.set_defaults(func=cmd_quote)

    stats = sub.add_parser("stats", help="检查索引完整性和覆盖范围")
    stats.add_argument("index")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(func=cmd_stats)
    return parser


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
