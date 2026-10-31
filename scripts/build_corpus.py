#!/usr/bin/env python3
"""Build one searchable corpus from a directory of PDF files."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return cleaned or "document"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp.replace(path)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="批量构建 PDF 语料索引")
    parser.add_argument("directory", help="包含 PDF 的目录")
    parser.add_argument("--out", required=True, help="语料输出目录")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument("--max-chars", type=int, default=900)
    args = parser.parse_args()

    source = Path(args.directory)
    if not source.is_dir():
        print(f"目录不存在: {source}", file=sys.stderr)
        raise SystemExit(1)
    pattern = "**/*.pdf" if args.recursive else "*.pdf"
    pdfs = sorted(path for path in source.glob(pattern) if path.is_file())
    if not pdfs:
        print("目录中没有 PDF", file=sys.stderr)
        raise SystemExit(1)

    output = Path(args.out)
    documents_dir = output / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)

    merged_chunks: list[dict] = []
    errors: list[dict] = []
    manifests: list[dict] = []
    seen_doc_ids: set[str] = set()

    for index, pdf in enumerate(pdfs, 1):
        source_hash = file_sha256(pdf)
        doc_dir = documents_dir / f"{safe_name(pdf.stem)}-{source_hash[:10]}"
        command = [
            sys.executable,
            str(SCRIPT_DIR / "build_pdf_rag.py"),
            str(pdf),
            "--out-dir",
            str(doc_dir),
            "--max-chars",
            str(args.max_chars),
        ]
        print(f"[{index}/{len(pdfs)}] {pdf.name}")
        result = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            errors.append({
                "file": pdf.name,
                "path": str(pdf.resolve()),
                "exit_code": result.returncode,
                "error": detail[-2000:],
            })
            print(f"  ERROR ({result.returncode}): {detail.splitlines()[-1] if detail else 'unknown'}")
            continue

        document_chunks = load_jsonl(doc_dir / "chunks.jsonl")
        if not document_chunks:
            errors.append({
                "file": pdf.name,
                "path": str(pdf.resolve()),
                "exit_code": 4,
                "error": "没有生成有效 chunk",
            })
            continue
        doc_id = str(document_chunks[0].get("doc_id", ""))
        if doc_id in seen_doc_ids:
            errors.append({
                "file": pdf.name,
                "path": str(pdf.resolve()),
                "exit_code": 0,
                "error": f"内容重复，已跳过 doc_id={doc_id}",
            })
            continue

        seen_doc_ids.add(doc_id)
        merged_chunks.extend(document_chunks)
        manifests.append({
            "file": pdf.name,
            "source_sha256": source_hash,
            "doc_id": doc_id,
            "chunks": len(document_chunks),
            "directory": str(doc_dir.relative_to(output)),
        })
        print(f"  OK: {len(document_chunks)} chunks")

    write_jsonl(output / "chunks.jsonl", merged_chunks)
    write_jsonl(output / "errors.jsonl", errors)

    by_type = Counter(chunk.get("chunk_type") for chunk in merged_chunks)
    meta = {
        "schema_version": 1,
        "source_directory": str(source.resolve()),
        "pdf_files": len(pdfs),
        "documents": len(manifests),
        "errors": len(errors),
        "chunks_total": len(merged_chunks),
        "by_type": dict(by_type),
        "manifests": manifests,
    }
    meta_path = output / "corpus_meta.json"
    meta_tmp = meta_path.with_name(meta_path.name + ".tmp")
    meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_tmp.replace(meta_path)

    print(
        f"完成: 文档={len(manifests)} chunks={len(merged_chunks)} errors={len(errors)}"
    )
    print(f"语料索引: {output / 'chunks.jsonl'}")


if __name__ == "__main__":
    main()
