#!/usr/bin/env python3
"""Evaluate retrieval quality against labeled PDF questions."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pdf_tools
from normalization import normalize_text


def load_labels(path: Path) -> list[dict]:
    labels = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            label = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no} 不是有效 JSON: {exc}") from exc
        for field in ("question", "gold_chunk_ids", "gold_pages", "answer_type"):
            if field not in label:
                raise ValueError(f"{path}:{line_no} 缺少字段 {field}")
        labels.append(label)
    return labels


def contains_value(text: str, value: str) -> bool:
    normalized_text = re.sub(r"\s+", "", normalize_text(text))
    normalized_value = re.sub(r"\s+", "", normalize_text(value))
    return bool(normalized_value) and normalized_value in normalized_text


def average(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def evaluate(labels: list[dict], chunks: list[dict], index_path: Path, k: int) -> dict:
    search_index, cache_path = pdf_tools.load_or_build_bm25_index(index_path, chunks)
    results = []
    type_rows: dict[str, list[dict]] = defaultdict(list)
    for label in labels:
        scores = pdf_tools.bm25_scores_from_index(label["question"], search_index)
        ranked = sorted(
            range(len(chunks)),
            key=lambda index: (
                -scores[index],
                chunks[index].get("page", 0),
                chunks[index].get("order", 0),
            ),
        )[:k]
        hits = [chunks[index] for index in ranked]
        hit_ids = [chunk["chunk_id"] for chunk in hits]
        gold_ids = [str(value) for value in label["gold_chunk_ids"]]
        gold_pages = {int(value) for value in label.get("gold_pages") or []}
        matched = set(hit_ids) & set(gold_ids)
        first_rank = next(
            (rank for rank, chunk_id in enumerate(hit_ids, 1) if chunk_id in gold_ids),
            0,
        )
        result = {
            "question": label["question"],
            "answer_type": label.get("answer_type") or "unknown",
            "hits": hit_ids,
            "recall": len(matched) / len(gold_ids) if gold_ids else 0.0,
            "reciprocal_rank": 1.0 / first_rank if first_rank else 0.0,
            "page_accuracy": (
                1.0 if hits and int(hits[0]["page"]) in gold_pages else 0.0
            ),
        }
        expected_values = label.get("expected_values") or []
        if expected_values:
            combined = "\n".join(str(chunk.get("text", "")) for chunk in hits)
            result["table_value_match"] = (
                1.0 if all(contains_value(combined, value) for value in expected_values)
                else 0.0
            )
        results.append(result)
        type_rows[result["answer_type"]].append(result)

    type_breakdown = {}
    for answer_type, rows in sorted(type_rows.items()):
        table_rows = [row["table_value_match"] for row in rows if "table_value_match" in row]
        type_breakdown[answer_type] = {
            "count": len(rows),
            "recall": average([row["recall"] for row in rows]),
            "mrr": average([row["reciprocal_rank"] for row in rows]),
            "page_accuracy": average([row["page_accuracy"] for row in rows]),
            "table_value_match": average(table_rows) if table_rows else None,
        }

    table_values = [
        row["table_value_match"] for row in results if "table_value_match" in row
    ]
    return {
        f"recall@{k}": average([row["recall"] for row in results]),
        "mrr": average([row["reciprocal_rank"] for row in results]),
        "page_accuracy": average([row["page_accuracy"] for row in results]),
        "table_value_match": average(table_values) if table_values else None,
        "questions": len(results),
        "k": k,
        "type_breakdown": type_breakdown,
        "results": results,
        "index_cache": str(cache_path),
    }


def make_template(chunks: list[dict], count: int) -> list[dict]:
    candidates = [
        chunk for chunk in chunks
        if chunk.get("chunk_type") in {"section", "table"}
    ]
    step = max(1, len(candidates) // max(count, 1))
    selected = candidates[::step][:count]
    return [
        {
            "question": "",
            "gold_chunk_ids": [chunk["chunk_id"]],
            "gold_pages": [chunk["page"]],
            "answer_type": "table" if chunk.get("chunk_type") == "table" else "fact",
            "expected_values": [],
            "notes": "填写真实问题；删除不需要的模板行",
        }
        for chunk in selected
    ]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="评估 PDF RAG 检索质量")
    parser.add_argument("labels", nargs="?", help="labels.jsonl")
    parser.add_argument("chunks", nargs="?", help="chunks.jsonl")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-recall", type=float)
    parser.add_argument("--template-out", default="", help="从 chunks 生成标注模板")
    parser.add_argument("--template-count", type=int, default=30)
    args = parser.parse_args()

    if args.template_out:
        if not args.chunks:
            if not args.labels:
                parser.error("生成模板需要 chunks.jsonl 路径")
            chunks_path = pdf_tools.resolve_index(args.labels)
        else:
            chunks_path = pdf_tools.resolve_index(args.chunks)
        chunks = pdf_tools.load_chunks(chunks_path)
        template = make_template(chunks, args.template_count)
        out = Path(args.template_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in template) + "\n",
            encoding="utf-8",
        )
        print(f"已写入 {len(template)} 条模板: {out}")
        return

    if not args.labels or not args.chunks:
        parser.error("需要 labels.jsonl 和 chunks.jsonl")
    if args.k <= 0:
        parser.error("--k 必须大于 0")
    chunks_path = pdf_tools.resolve_index(args.chunks)
    try:
        labels = load_labels(Path(args.labels))
    except (OSError, ValueError) as exc:
        print(f"标签读取失败: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if not labels:
        print("标签文件为空", file=sys.stderr)
        raise SystemExit(1)
    chunks = pdf_tools.load_chunks(chunks_path)
    report = evaluate(labels, chunks, chunks_path, args.k)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"questions = {report['questions']}  k = {report['k']}")
        print(f"recall@{args.k} = {report[f'recall@{args.k}']:.4f}")
        print(f"mrr = {report['mrr']:.4f}")
        print(f"page_accuracy = {report['page_accuracy']:.4f}")
        value_match = report["table_value_match"]
        print(f"table_value_match = {'-' if value_match is None else f'{value_match:.4f}'}")
        print("type_breakdown:")
        for answer_type, values in report["type_breakdown"].items():
            print(f"  {answer_type}: {values}")
    if args.min_recall is not None and report[f"recall@{args.k}"] < args.min_recall:
        print(
            f"recall@{args.k} {report[f'recall@{args.k}']:.4f} "
            f"低于门槛 {args.min_recall:.4f}",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
