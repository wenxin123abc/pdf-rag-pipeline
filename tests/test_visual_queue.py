from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from selftest import make_sample  # noqa: E402


class VisualQueueTests(unittest.TestCase):
    def test_queue_crops_images_and_creates_model_ready_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "sample.pdf"
            index_dir = root / "index"
            queue = root / "queue"
            make_sample(pdf)
            build = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "build_pdf_rag.py"),
                    str(pdf),
                    "--out-dir",
                    str(index_dir),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(0, build.returncode, build.stderr)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "visual_queue.py"),
                    str(index_dir / "chunks.jsonl"),
                    "--pdf",
                    str(pdf),
                    "--out",
                    str(queue),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

            self.assertEqual(0, result.returncode, result.stderr)
            prompts = json.loads((queue / "prompts.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(prompts))
            self.assertTrue((queue / prompts[0]["png_path"]).exists())
            self.assertIn("prompt", prompts[0])
            descriptions = json.loads(
                (queue / "descriptions.template.json").read_text(encoding="utf-8")
            )
            self.assertEqual(prompts[0]["image_id"], next(iter(descriptions)))


if __name__ == "__main__":
    unittest.main()
