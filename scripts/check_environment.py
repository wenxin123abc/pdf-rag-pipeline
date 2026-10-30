#!/usr/bin/env python3
"""Report whether the required and optional PDF processing dependencies exist."""
from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import sys


def main() -> None:
    report = {
        "python": sys.version.split()[0],
        "python_compatible": sys.version_info >= (3, 10),
        "platform": platform.platform(),
        "pymupdf": {"available": False},
        "optional": {
            "pdfplumber": bool(importlib.util.find_spec("pdfplumber")),
            "pymupdf_layout": bool(importlib.util.find_spec("pymupdf_layout")),
            "tesseract": shutil.which("tesseract"),
        },
    }
    try:
        import pymupdf
        report["pymupdf"] = {
            "available": True,
            "module": "pymupdf",
            "version": getattr(pymupdf, "VersionBind", ""),
            "engine": getattr(pymupdf, "mupdf_version", ""),
            "find_tables": hasattr(pymupdf.Page, "find_tables"),
        }
    except ImportError:
        try:
            import fitz as pymupdf
            report["pymupdf"] = {
                "available": True,
                "module": "fitz",
                "version": getattr(pymupdf, "VersionBind", ""),
                "engine": getattr(pymupdf, "mupdf_version", ""),
                "find_tables": hasattr(pymupdf.Page, "find_tables"),
            }
        except ImportError:
            pass

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["python_compatible"]:
        sys.exit("需要 Python 3.10 或更高版本")
    if not report["pymupdf"]["available"]:
        sys.exit("缺少 PyMuPDF，请运行: python -m pip install pymupdf")


if __name__ == "__main__":
    main()
