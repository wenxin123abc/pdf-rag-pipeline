"""Stable command-line exit codes shared by the PDF tools."""
from __future__ import annotations

import sys

EXIT_USAGE = 1
EXIT_SCANNED = 2
EXIT_ENCRYPTED = 3
EXIT_NO_TEXT = 4


def fail(message: str, code: int = EXIT_USAGE) -> None:
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    print(message, file=sys.stderr)
    raise SystemExit(code)
