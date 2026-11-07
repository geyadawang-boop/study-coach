#!/usr/bin/env python3
"""Probe a PDF to decide the import chain before any OCR.

Samples the first N pages and N pages around the middle, counts readable
characters and embedded images per page, and classifies each sampled page as
A (text layer: direct extraction) or B (scanned/garbled: render + OCR).
Verdicts are per page because a mixed PDF must not be judged as a whole.
Protocol: references/paper-intake.md.

Usage:
  python pdf_probe.py paper.pdf [--pages 3] [--min-chars 200] [--json]

Exit codes: 0 = probed; 2 = file missing / not readable as PDF;
3 = PyMuPDF (fitz) missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def sample_pages(total: int, count: int) -> list[int]:
    if total <= 0:
        return []
    head = list(range(1, min(count, total) + 1))
    center = (total + 1) // 2
    start = max(1, center - count // 2)
    middle = list(range(start, min(total, start + count - 1) + 1))
    return sorted(set(head + middle))


def probe(path: Path, count: int, min_chars: int) -> dict:
    import fitz  # PyMuPDF

    with fitz.open(path) as doc:
        total = doc.page_count
        pages = sample_pages(total, count)
        rows = []
        for number in pages:
            page = doc.load_page(number - 1)
            raw = page.get_text("text") or ""
            chars = len("".join(raw.split()))
            replacement = raw.count("\ufffd")
            garbled = chars > 0 and replacement / chars > 0.3
            rows.append({
                "page": number,
                "chars": chars,
                "images": len(page.get_images(full=True)),
                "verdict": "A" if chars >= min_chars and not garbled else "B",
            })

    verdicts = {row["verdict"] for row in rows}
    if verdicts == {"A"}:
        overall, hint = "A", "文本层可直接抽文本；公式由模型转 LaTeX，标 conf=high"
    elif verdicts == {"B"}:
        overall, hint = "B", "按扫描链路：250–300 dpi 渲染 → 版面分析 → OCR"
    elif not verdicts:
        overall, hint = "B", "PDF 没有可读页；先确认文件是否损坏"
    else:
        overall, hint = "mixed", "混合卷：逐页判定，A 页直抽、B 页渲染后 OCR；不要整本一刀切"
    return {
        "path": str(path),
        "pages": total,
        "sampled": rows,
        "verdict": overall,
        "hint": hint,
        "min_chars": min_chars,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="待判定的 PDF")
    parser.add_argument("--pages", type=int, default=3, help="头部与中段各抽样页数")
    parser.add_argument("--min-chars", type=int, default=200, help="判为文本层的每页最少字符数")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.pdf)
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    with path.open("rb") as handle:
        head = handle.read(1024)
    if b"%PDF-" not in head:
        print(f"不是 PDF（前 1024 字节缺少 %PDF- 头）：{path}", file=sys.stderr)
        return 2
    try:
        result = probe(path, max(1, args.pages), max(1, args.min_chars))
    except ImportError:
        print("需要 PyMuPDF：pip install pymupdf", file=sys.stderr)
        return 3
    except Exception as exc:  # 损坏、加密、不是 PDF
        print(f"无法读取 PDF（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"sampled={len(result['sampled'])} min_chars={result['min_chars']}")
        for row in result["sampled"]:
            print(f"  p{row['page']} chars={row['chars']} images={row['images']} {row['verdict']}")
        print(f"verdict={result['verdict']}")
        print(f"hint={result['hint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
