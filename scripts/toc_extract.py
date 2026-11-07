#!/usr/bin/env python3
"""Extract a chapter tree from the printed table of contents.

Scanned PDFs usually have no embedded outline, but the printed 目录 pages
have the chapter/section titles and page numbers. This script reads those
pages (text layer first, OCR only when there is no text layer) and emits a
`toc.json` tree that `chapter_diff.py` accepts directly. The model then
reviews the tree (low-confidence lines are listed) instead of hand-copying
chapters into Markdown.

Usage:
  python toc_extract.py book.pdf --pages 2-4 --out toc.json
  python toc_extract.py book.pdf --pages 2-4 --json
  python toc_extract.py book.pdf --pages 2-4 --mode ocr --dpi 220

Exit codes: 0 = tree built; 1 = no TOC lines recognised; 2 = input unreadable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

CHAPTER = re.compile(r"^\s*(第\s*[一二三四五六七八九十百千0-9]+\s*[章篇编部])\s*(.*)$")
SECTION = re.compile(r"^\s*(\d+(?:\.\d+){1,2})\s*(.*)$")
TRAILING_PAGE = re.compile(r"(?:[.·…]{2,}|\s)\s*(\d{1,4})\s*$")
LOW_CONF = 0.90


def parse_pages(value: str) -> list[int]:
    pages: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            pages.extend(range(int(start), int(end) + 1))
        else:
            pages.append(int(part))
    if not pages:
        raise ValueError("--pages 为空")
    return sorted(set(pages))


def text_lines(path: Path, pages: list[int]) -> tuple[list[dict], bool]:
    """Return [{text, score, page}], used_text_layer."""
    import fitz

    lines: list[dict] = []
    with fitz.open(path) as doc:
        for page_no in pages:
            if page_no < 1 or page_no > doc.page_count:
                raise ValueError(f"page {page_no} 超出范围 1..{doc.page_count}")
            text = doc.load_page(page_no - 1).get_text("text")
            for line in text.splitlines():
                if line.strip():
                    lines.append({"text": line, "score": 1.0, "page": page_no})
    return lines, bool(lines)


def ocr_lines(path: Path, pages: list[int], dpi: int) -> list[dict]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ocr_page import run_ocr, to_image

    lines: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for page_no in pages:
            image = to_image(path, page_no, dpi, Path(tmp) / f"p{page_no}.png")
            for row in run_ocr(image):
                lines.append({"text": row["text"], "score": row["score"], "page": page_no})
    return lines


def parse_tree(lines: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    tree: list[dict] = []
    pending: list[dict] = []
    suspects: list[dict] = []
    warnings: list[str] = []
    seen: set[tuple[int, str]] = set()

    for line in lines:
        text = line["text"].strip()
        level = 0
        number = ""
        title = ""
        match = CHAPTER.match(text)
        if match:
            level, number, title = 1, match.group(1), match.group(2).strip()
        else:
            match = SECTION.match(text)
            if match:
                level, number, title = 2, match.group(1), match.group(2).strip()
        if not level:
            continue

        page_no = None
        trail = TRAILING_PAGE.search(title)
        if trail:
            page_no = int(trail.group(1))
            title = title[:trail.start()].strip(" .·…\t")
        title = re.sub(r"\s+", " ", title).strip()
        display = f"{number} {title}".strip() if title else number

        if len(title) < 2 or re.fullmatch(r"[\d.]+", title):
            suspects.append({"text": text, "page": line["page"],
                             "reason": "标题缺失或只剩数字（目录文本层错位）"})
            continue

        key = (level, display)
        if key in seen:
            continue
        seen.add(key)
        row = {"level": level, "title": display, "page": page_no, "ocr_page": line["page"]}
        if line["score"] < LOW_CONF:
            row["conf"] = line["score"]
            pending.append({"title": display, "page": line["page"], "conf": line["score"]})
        tree.append(row)

    if not tree:
        warnings.append("未识别到可用的「第X章 标题 / 1.1 标题」目录行")
    if pending:
        warnings.append(f"{len(pending)} 行置信度低于 {LOW_CONF}，请校对后再用")
    if suspects:
        warnings.append(
            f"{len(suspects)} 行目录文本错位或缺标题，已跳过；"
            "请让模型按 raw 行重建 toc.json 后再 diff"
        )
    return tree, pending, suspects, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="含印刷目录的 PDF")
    parser.add_argument("--pages", required=True, help="目录页，如 2-4 或 2,3,5")
    parser.add_argument("--mode", choices=["auto", "text", "ocr"], default="auto",
                        help="auto：有文本层直读，否则 OCR")
    parser.add_argument("--dpi", type=int, default=220, help="OCR 渲染 dpi（默认 220）")
    parser.add_argument("--out", help="写入 toc.json（默认打印到标准输出）")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args()

    path = Path(args.pdf)
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    try:
        pages = parse_pages(args.pages)
    except ValueError as exc:
        print(f"--pages 无效：{exc}", file=sys.stderr)
        return 2
    try:
        import fitz  # noqa: F401
    except ImportError:
        print("需要 PyMuPDF：pip install pymupdf", file=sys.stderr)
        return 2

    try:
        if args.mode == "text":
            lines, _ = text_lines(path, pages)
        elif args.mode == "ocr":
            lines = ocr_lines(path, pages, args.dpi)
        else:
            lines, has_text = text_lines(path, pages)
            lines = lines if has_text else ocr_lines(path, pages, args.dpi)
    except ImportError:
        print("OCR 需要 rapidocr_onnxruntime：pip install rapidocr_onnxruntime",
              file=sys.stderr)
        return 2
    except Exception as exc:  # 损坏、加密、页码越界
        print(f"无法读取目录页（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 2

    tree, pending, suspects, warnings = parse_tree(lines)
    data = {
        "source": str(path),
        "pages": pages,
        "tree": tree,
        "pending": pending,
        "suspects": suspects,
        "quality": {"parsed": len(tree), "pending": len(pending),
                    "suspect": len(suspects), "raw": len(lines)},
        "warnings": warnings,
        "raw": lines,
    }
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if not tree:
        if args.out or args.json:
            print(text)
        message = f"未识别到可用目录行：{path} pages={pages}"
        if suspects:
            message += f"；{len(suspects)} 行错位，raw 已输出，请模型重建 toc.json"
        print(message, file=sys.stderr)
        return 1

    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {target} chapters={len(tree)} pending={len(pending)} "
              f"suspect={len(suspects)}")
    elif args.json:
        print(text)
    else:
        print(f"chapters={len(tree)} pending={len(pending)} suspect={len(suspects)} "
              f"pages={pages}")
        for row in tree:
            page = "" if row["page"] is None else f"  p{row['page']}"
            print(f"{'#' * row['level']} {row['title']}{page}")
        for item in warnings:
            print(f"warn={item}")
        print("hint=校对 toc.json 后：python chapter_diff.py 旧.toc.json 新.toc.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
