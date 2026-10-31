#!/usr/bin/env python3
"""Generate a self-contained HTML preview of PDF chunks and page highlights."""
from __future__ import annotations

import argparse
import base64
import html
import json
import sys
from pathlib import Path

import pymupdf

import extract_structure
import pdf_tools


def render_pages(doc, page_numbers: set[int], scale: float) -> dict[int, dict]:
    pages = {}
    for page_number in sorted(page_numbers):
        page = doc[page_number - 1]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        encoded = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
        pages[page_number] = {
            "width": float(page.rect.width),
            "height": float(page.rect.height),
            "data_url": f"data:image/png;base64,{encoded}",
        }
    return pages


def select_chunks(
    chunks_path: Path,
    chunks: list[dict],
    query: str,
    limit: int,
) -> list[tuple[float | None, dict]]:
    if not query:
        preferred = [
            chunk for chunk in chunks
            if chunk.get("chunk_type") in {"section", "table", "image"}
        ]
        preferred.sort(key=lambda chunk: chunk.get("order", 0))
        return [(None, chunk) for chunk in preferred[:limit]]

    index, _ = pdf_tools.load_or_build_bm25_index(chunks_path, chunks)
    scores = pdf_tools.bm25_scores_from_index(query, index)
    ranked = sorted(
        range(len(chunks)),
        key=lambda position: (
            -scores[position],
            chunks[position].get("page", 0),
            chunks[position].get("order", 0),
        ),
    )
    return [
        (scores[position], chunks[position])
        for position in ranked
        if scores[position] > 0
    ][:limit]


def build_html(
    pdf_name: str,
    query: str,
    selected: list[tuple[float | None, dict]],
    page_renders: dict[int, dict],
) -> str:
    page_json = json.dumps(page_renders, ensure_ascii=False)
    cards = []
    for index, (score, chunk) in enumerate(selected):
        section = " > ".join(chunk.get("section_path") or [])
        score_label = f"{score:.2f}" if score is not None else ""
        cards.append(
            f"""
            <button class="result" onclick="showItem({index})" data-bbox="{html.escape(json.dumps(chunk.get('bbox', [])))}">
              <span class="meta">p{chunk['page']} · {chunk['chunk_type']} {score_label}</span>
              <strong>{html.escape(chunk.get('doc_title', ''))}</strong>
              <span>{html.escape(section or '无章节路径')}</span>
              <small>{html.escape(pdf_tools.snippet(chunk['text'], query, 55) if query else chunk['text'][:180])}</small>
            </button>
            """
        )
    items = [
        {
            "page": chunk["page"],
            "bbox": chunk.get("bbox") or [0, 0, 0, 0],
            "chunk_id": chunk["chunk_id"],
        }
        for _, chunk in selected
    ]
    items_json = json.dumps(items, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PDF RAG Preview - {html.escape(pdf_name)}</title>
  <style>
    :root {{ color-scheme: light; font-family: ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #eef2f7; color: #172033; }}
    header {{ padding: 18px 24px; background: #172033; color: white; }}
    header h1 {{ margin: 0 0 4px; font-size: 20px; }}
    header p {{ margin: 0; color: #b8c4d9; }}
    main {{ display: grid; grid-template-columns: minmax(280px, 34%) 1fr; min-height: calc(100vh - 78px); }}
    aside {{ padding: 16px; overflow: auto; border-right: 1px solid #cbd5e1; }}
    .result {{ display: grid; gap: 6px; width: 100%; margin-bottom: 10px; padding: 13px; text-align: left; border: 1px solid #cbd5e1; border-radius: 10px; background: white; color: inherit; cursor: pointer; }}
    .result:hover {{ border-color: #2563eb; box-shadow: 0 4px 16px #2563eb1f; }}
    .meta {{ color: #2563eb; font-size: 12px; font-weight: 700; }}
    small {{ color: #526177; line-height: 1.45; }}
    section.viewer {{ display: flex; align-items: flex-start; justify-content: center; overflow: auto; padding: 24px; }}
    .page {{ position: relative; max-width: 100%; box-shadow: 0 12px 38px #1720332e; background: white; }}
    .page img {{ display: block; width: 100%; height: auto; }}
    .highlight {{ position: absolute; border: 2px solid #dc2626; background: #ef444433; pointer-events: none; }}
    .empty {{ padding: 40px; color: #64748b; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(pdf_name)}</h1>
    <p>{'检索：' + html.escape(query) if query else '按原文顺序预览 chunk 与页码定位'}</p>
  </header>
  <main>
    <aside>{''.join(cards) or '<div class="empty">没有可展示的 chunk。</div>'}</aside>
    <section class="viewer">
      <div class="page" id="page"><div class="empty">选择左侧片段查看原文位置</div></div>
    </section>
  </main>
  <script>
    const pages = {page_json};
    const items = {items_json};
    function showItem(index) {{
      const item = items[index];
      const page = pages[String(item.page)];
      if (!page) return;
      const host = document.getElementById('page');
      const [x0, y0, x1, y1] = item.bbox;
      const left = Math.max(0, x0 / page.width * 100);
      const top = Math.max(0, y0 / page.height * 100);
      const width = Math.max(0.2, (x1 - x0) / page.width * 100);
      const height = Math.max(0.2, (y1 - y0) / page.height * 100);
      host.innerHTML = `<img src="${{page.data_url}}" alt="page ${{item.page}}">
        <div class="highlight" style="left:${{left}}%;top:${{top}}%;width:${{width}}%;height:${{height}}%"></div>`;
    }}
    if (items.length) showItem(0);
  </script>
</body>
</html>
"""


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="生成离线 PDF 原文引用预览")
    parser.add_argument("chunks", help="chunks.jsonl")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True, help="输出 HTML")
    parser.add_argument("--query", default="")
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--scale", type=float, default=1.35)
    parser.add_argument("--password", default="")
    args = parser.parse_args()

    if args.k <= 0 or args.scale <= 0:
        parser.error("--k 和 --scale 必须大于 0")
    chunks_path = pdf_tools.resolve_index(args.chunks)
    chunks = pdf_tools.load_chunks(chunks_path)
    selected = select_chunks(chunks_path, chunks, args.query, args.k)
    page_numbers = {int(chunk["page"]) for _, chunk in selected}

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        sys.exit(f"PDF 不存在: {pdf_path}")
    fitz = extract_structure.load_pymupdf()
    doc = extract_structure.open_pdf(fitz, pdf_path, args.password)
    try:
        page_renders = render_pages(doc, page_numbers, args.scale)
    finally:
        doc.close()

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        build_html(pdf_path.name, args.query, selected, page_renders),
        encoding="utf-8",
    )
    print(f"预览已写入: {output}")
    print(f"片段数={len(selected)} 页面数={len(page_renders)}")


if __name__ == "__main__":
    main()
