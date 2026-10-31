from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class CliContractTests(unittest.TestCase):
    def test_read_supports_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunks.jsonl"
            record = {
                "chunk_id": "c1", "doc_id": "doc", "doc_title": "Guide",
                "page": 1, "chunk_type": "page", "order": 1,
                "section_path": [], "char_len": 4, "text": "body",
                "embed_text": "body", "quote_anchor": "page=1",
            }
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "pdf_tools.py"),
                    "read",
                    str(path),
                    "--page",
                    "1",
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("c1", json.loads(result.stdout)["chunk_id"])


if __name__ == "__main__":
    unittest.main()
