#!/usr/bin/env python3
"""Import PyMuPDF without letting its version banner pollute JSON stdout.

PyMuPDF 1.28 prints a deprecation banner to stdout when the legacy alias
`import fitz` is used. Several scripts write JSON to stdout, so that banner
breaks downstream parsing (toc_extract, chapter_diff self-checks included).

Import the canonical `pymupdf` module first; if only the legacy alias exists,
import it with stdout redirected so any banner cannot leak.

Usage:
    from fitz_compat import fitz

Raises ImportError when PyMuPDF is absent, matching `import fitz`.
"""

from __future__ import annotations

import contextlib
import io


def _load():
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            import pymupdf
        except ImportError:
            try:
                import fitz
            except ImportError as exc:
                raise ImportError("需要 PyMuPDF：pip install pymupdf") from exc
            return fitz
        return pymupdf


fitz = _load()
