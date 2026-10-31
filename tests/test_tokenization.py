from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pdf_tools  # noqa: E402


class TokenizationTests(unittest.TestCase):
    def test_nfkc_and_traditional_chinese_are_normalized(self) -> None:
        tokens = pdf_tools.tokenize("１００萬元，發票稅費")

        self.assertIn("100", tokens)
        self.assertIn("发票", tokens)
        self.assertIn("税费", tokens)

    def test_single_chinese_character_has_lower_weight_but_remains_searchable(self) -> None:
        weights = pdf_tools.tokenize_weighted("图中税额")

        self.assertAlmostEqual(0.4, weights["图"])
        self.assertAlmostEqual(1.0, weights["图中"])


if __name__ == "__main__":
    unittest.main()
