from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def create_pdf(path: Path, title: str, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), title, fontsize=18, fontname="helv")
    page.insert_textbox(
        pymupdf.Rect(72, 100, 520, 220),
        text,
        fontsize=11,
        fontname="helv",
    )
    doc.save(path)
    doc.close()


class CorpusTests(unittest.TestCase):
    def test_corpus_build_keeps_valid_documents_and_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "pdfs"
            output = root / "corpus"
            source.mkdir()
            create_pdf(
                source / "alpha.pdf",
                "Alpha Guide",
                "shared payment policy alpha with enough extractable text for reliable indexing",
            )
            create_pdf(
                source / "beta.pdf",
                "Beta Manual",
                "shared payment policy beta with enough extractable text for reliable indexing",
            )
            (source / "broken.pdf").write_bytes(b"not a pdf")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_corpus.py"),
                    str(source),
                    "--out",
                    str(output),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            chunks = [
                json.loads(line)
                for line in (output / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            errors = [
                json.loads(line)
                for line in (output / "errors.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertGreaterEqual(len({chunk["doc_title"] for chunk in chunks}), 2)
            self.assertEqual(1, len(errors))
            self.assertEqual("broken.pdf", errors[0]["file"])

            filtered = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "pdf_tools.py"),
                    "search",
                    str(output / "chunks.jsonl"),
                    "shared payment policy",
                    "--doc",
                    "Alpha",
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(0, filtered.returncode, filtered.stderr)
            hits = json.loads(filtered.stdout)
            self.assertTrue(hits)
            self.assertTrue(all("Alpha" in hit["doc_title"] for hit in hits))


if __name__ == "__main__":
    unittest.main()
