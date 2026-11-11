#!/usr/bin/env python3
"""OCR one page of a PDF (or one image) for the B/C import links.

Renders the page at the given dpi, then runs RapidOCR (offline ONNX models).
Recognized text is returned as-is: math is NOT repaired here, so every line
with score < 0.90 must be flagged 待核 by the caller
(see references/paper-intake.md).

Usage:
  python ocr_page.py paper.pdf --page 50 [--dpi 200] [--json] [--image out.png]
  python ocr_page.py photo.jpg [--json]

Exit codes: 0 = OCR done; 2 = file/page unreadable;
3 = PyMuPDF or RapidOCR missing.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
LOW_CONF = 0.90


def to_image(path: Path, page_no: int, dpi: int, image_out: Path | None) -> Path:
    from fitz_compat import fitz  # PyMuPDF，经兼容层避免 1.28 弃用横幅污染 stdout

    with fitz.open(path) as doc:
        if page_no < 1 or page_no > doc.page_count:
            raise ValueError(f"page {page_no} 超出范围 1..{doc.page_count}")
        pix = doc.load_page(page_no - 1).get_pixmap(dpi=dpi)

    target = image_out or Path(tempfile.gettempdir()) / f"ocr-{path.stem}-p{page_no}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    pix.save(target)
    return target


def run_ocr_lines(image: Path) -> list[dict]:
    """OCR with line boxes: [{"text","score","box":[x0,y0,x1,y1]}] in pixels."""
    from rapidocr_onnxruntime import RapidOCR

    result = RapidOCR()(str(image))
    rows = result[0] if isinstance(result, tuple) else result
    lines = []
    for row in rows or []:
        box = row[0] if row[0] else []
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        lines.append({
            "text": str(row[1]).strip(),
            "score": round(float(row[2]), 4),
            "box": [min(xs), min(ys), max(xs), max(ys)] if xs and ys else [0.0, 0.0, 0.0, 0.0],
        })
    return lines


def run_ocr(image: Path) -> list[dict]:
    return [{"text": line["text"], "score": line["score"]} for line in run_ocr_lines(image)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="PDF 或图片")
    parser.add_argument("--page", type=int, default=1, help="PDF 页码（1 起）")
    parser.add_argument("--dpi", type=int, default=200, help="渲染 dpi（扫描件建议 250–300）")
    parser.add_argument("--image", help="把渲染图另存到该路径（便于裁图入 fig/）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2

    rendered: Path | None = None
    try:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            image = path
            page = None
        else:
            if b"%PDF-" not in path.open("rb").read(1024):
                print(f"不是 PDF 也不是支持的图片：{path}", file=sys.stderr)
                return 2
            image = to_image(path, args.page, args.dpi,
                             Path(args.image) if args.image else None)
            if not args.image:
                rendered = image  # 临时渲染图用完即删，不在系统临时目录堆积
            page = args.page
    except ImportError:
        print("需要 PyMuPDF：pip install pymupdf", file=sys.stderr)
        return 3
    except Exception as exc:  # 损坏、加密、页码越界
        print(f"无法读取输入（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 2

    try:
        lines = run_ocr(image)
    except ImportError:
        print("需要 RapidOCR：pip install rapidocr-onnxruntime", file=sys.stderr)
        return 3
    except Exception as exc:  # 图片损坏或无法解码
        print(f"OCR 失败（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 2
    finally:
        if rendered is not None and rendered.exists():
            rendered.unlink()

    chars = sum(len(line["text"]) for line in lines)
    low = [line for line in lines if line["score"] < LOW_CONF]
    summary = {
        "source": str(path),
        "page": page,
        "dpi": args.dpi if page else None,
        "image": str(image),
        "lines": lines,
        "chars": chars,
        "low_conf": len(low),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"image={image} chars={chars} lines={len(lines)} low_conf={len(low)}")
        for line in lines:
            print(f"[{line['score']:.2f}] {line['text']}")
        if low:
            print(f"hint={len(low)} 行置信度 < {LOW_CONF}，按 paper-intake.md 标「待核」，不进评分")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
