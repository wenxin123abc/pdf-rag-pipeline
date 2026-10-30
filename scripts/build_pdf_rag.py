#!/usr/bin/env python3
"""Run PDF detection, structure extraction, chunking, and index validation."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def run(script: str, *args: str) -> None:
    command = [sys.executable, str(SCRIPT_DIR / script), *args]
    result = subprocess.run(command)
    if result.returncode:
        raise SystemExit(result.returncode)


def main() -> None:
    ap = argparse.ArgumentParser(description="一键构建可溯源的 PDF RAG 索引")
    ap.add_argument("pdf", help="输入 PDF")
    ap.add_argument("--out-dir", default="", help="输出目录，默认 <pdf名>-rag")
    ap.add_argument("--max-pages", type=int, default=0, help="类型检测只抽样前 N 页")
    ap.add_argument("--columns", type=int, default=0, help="手动指定 1-4 栏；默认自动检测")
    ap.add_argument("--max-chars", type=int, default=900, help="section chunk 字符上限")
    ap.add_argument("--password", default="", help="加密 PDF 的密码")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        sys.exit(f"文件不存在: {pdf}")
    out_dir = Path(args.out_dir) if args.out_dir else pdf.with_name(pdf.stem + "-rag")
    out_dir.mkdir(parents=True, exist_ok=True)
    type_path = out_dir / "type.json"
    structure_path = out_dir / "structure.json"
    chunks_path = out_dir / "chunks.jsonl"

    detect_args = [str(pdf), "--out", str(type_path)]
    if args.max_pages > 0:
        detect_args.extend(["--max-pages", str(args.max_pages)])
    if args.password:
        detect_args.extend(["--password", args.password])
    run("detect_pdf_type.py", *detect_args)

    report = json.loads(type_path.read_text(encoding="utf-8"))
    if report.get("doc_type") == "scanned":
        print("检测为扫描型 PDF。本技能不内置 OCR；请先接入 MinerU、OCR 或视觉文档解析。")
        raise SystemExit(2)
    columns = args.columns or int(report.get("columns", {}).get("max_detected") or 1)
    if not 1 <= columns <= 4:
        sys.exit("自动栏数超出 1-4；请用 --columns 指定")

    extract_args = [
        str(pdf),
        "--out", str(structure_path),
        "--columns", str(columns),
    ]
    if args.password:
        extract_args.extend(["--password", args.password])
    run("extract_structure.py", *extract_args)
    run(
        "build_chunks.py",
        str(structure_path),
        "--out", str(chunks_path),
        "--max-chars", str(args.max_chars),
    )
    run("pdf_tools.py", "stats", str(chunks_path))
    print(f"\n完成。索引目录: {out_dir}")
    if report.get("doc_type") == "mixed":
        print("注意：这是 mixed PDF；无文本页和图表仍需 OCR/视觉补充。")


if __name__ == "__main__":
    main()
