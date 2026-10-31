from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pdf_tools  # noqa: E402


def build_chunks(count: int) -> list[dict]:
    chunks = []
    for index in range(count):
        text = f"document {index} payment policy traceable evidence"
        chunks.append({
            "chunk_id": f"pdf-{index:016x}-p1-s1",
            "doc_id": f"pdf-{index % 100:016x}",
            "doc_title": f"Document {index % 100}",
            "source_sha256": f"{index:064x}"[-64:],
            "page": 1,
            "chunk_type": "section",
            "order": index,
            "section_path": ["Payment"],
            "text": text,
            "embed_text": text,
            "quote_anchor": "page=1",
        })
    return chunks


class SearchCacheTests(unittest.TestCase):
    def test_cache_builds_once_and_invalidates_after_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunks.jsonl"
            chunks = build_chunks(5000)
            path.write_text(
                "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
                encoding="utf-8",
            )

            started = time.perf_counter()
            first, first_cache = pdf_tools.load_or_build_bm25_index(path, chunks)
            first_elapsed = time.perf_counter() - started

            started = time.perf_counter()
            second, second_cache = pdf_tools.load_or_build_bm25_index(path, chunks)
            second_elapsed = time.perf_counter() - started

            self.assertLess(first_elapsed, 1.0)
            self.assertLess(second_elapsed, 0.5)
            self.assertEqual(first_cache, second_cache)
            self.assertTrue(first_cache.exists())
            self.assertEqual(first["source_sha256"], second["source_sha256"])

            chunks[0]["text"] = "changed tax invoice evidence"
            chunks[0]["embed_text"] = chunks[0]["text"]
            path.write_text(
                "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks) + "\n",
                encoding="utf-8",
            )
            rebuilt, rebuilt_cache = pdf_tools.load_or_build_bm25_index(path, chunks)

            self.assertNotEqual(first["source_sha256"], rebuilt["source_sha256"])
            self.assertNotEqual(first_cache, rebuilt_cache)
            self.assertIn("tax", rebuilt["postings"])


if __name__ == "__main__":
    unittest.main()
