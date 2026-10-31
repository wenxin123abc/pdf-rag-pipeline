from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from selftest import make_sample  # noqa: E402


class PreviewTests(unittest.TestCase):
    def test_preview_is_offline_and_contains_highlight_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "sample.pdf"
            index = root / "index"
            html = root / "preview.html"
            make_sample(pdf)
            build = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_pdf_rag.py"),
                    str(pdf),
                    "--out-dir",
                    str(index),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(0, build.returncode, build.stderr)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "preview.py"),
                    str(index / "chunks.jsonl"),
                    "--pdf",
                    str(pdf),
                    "--out",
                    str(html),
                    "--query",
                    "payment terms",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            content = html.read_text(encoding="utf-8")
            self.assertIn("data:image/png;base64,", content)
            self.assertIn('data-bbox="', content)
            self.assertIn("payment terms", content.lower())
            self.assertNotIn("https://", content)


if __name__ == "__main__":
    unittest.main()
