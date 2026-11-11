#!/usr/bin/env python3
"""Split a text or OCR page into question candidates (draft JSONL).

Implements the rule order in references/paper-intake.md: question-number
regex first, then blank lines as a weak boundary. Output is a DRAFT: missing
`type`/`points` and low confidence are flagged for the caller to complete
before running intake_check.py. Ambiguous boundaries are reported, never
force-cut.

Input forms:
  1. Plain text / Markdown: `python slice_questions.py page.txt --paper MID`
  2. JSON from ocr_page.py --json (single page or a list of pages):
     `python slice_questions.py page.json --paper MID --out draft.jsonl`

Exit codes: 0 = candidates emitted; 1 = no question found; 2 = input unreadable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

Q_START = re.compile(r"^\s*(?:第\s*)?(\d+)\s*[.、．]\s*(?!\d)")
Q_ALT = re.compile(r"^\s*第\s*(\d+)\s*题\s*[.、:：]?")
MD_Q = re.compile(r"^\s*#{1,6}\s*(?:第\s*)?(\d+)\s*(?:题|[.、．])\s*(?!\d)")
SUB = re.compile(r"^\s*[（(]\s*(\d+)\s*[）)]")
SECTION = re.compile(r"^\s*(?:[一二三四五六七八九十]+|[IVX]+)\s*[、.．]")
POINTS = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]")
LOW_CONF = 0.90


def infer_type(text: str) -> str:
    if "证明" in text:
        return "证明"
    if "选择" in text:
        return "选择"
    if "填空" in text:
        return "填空"
    if "判断" in text:
        return "判断"
    if any(word in text for word in ("计算", "求", "极限", "积分", "导数", "解")):
        return "计算"
    return "未定"


def read_lines(path: Path) -> tuple[list[dict], list[str]]:
    """Return [{text, score, page}], and warnings."""
    warnings: list[str] = []
    if path.suffix.lower() != ".json":
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return [{"text": line, "score": 1.0, "page": None} for line in text.splitlines()], warnings

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    pages = data if isinstance(data, list) else [data]
    lines: list[dict] = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("lines"), list):
            raise ValueError("JSON 缺少 lines 数组（需 ocr_page.py --json 的输出）")
        page_no = page.get("page")
        for row in page["lines"]:
            if isinstance(row, dict):
                raw_score = row.get("score")
                try:
                    score = 1.0 if raw_score is None else float(raw_score)
                except (TypeError, ValueError):
                    score = 1.0
                lines.append({
                    "text": str(row.get("text") or ""),
                    "score": score,
                    "page": page_no,
                })
    if not lines:
        warnings.append("OCR JSON 没有任何文本行")
    return lines, warnings


def split(lines: list[dict]) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    questions: list[dict] = []
    current: dict | None = None
    section = ""
    suspect = 0

    def flush() -> None:
        nonlocal current, suspect
        if current is None:
            return
        text = " ".join(line["text"].strip() for line in current["lines"] if line["text"].strip())
        if not text:
            current = None
            return
        scores = [line["score"] for line in current["lines"] if line["text"].strip()]
        conf = round(min(scores), 4) if scores else 1.0
        pages = [line["page"] for line in current["lines"] if line["page"]]
        points = POINTS.search(text[:120])
        flags: list[str] = []
        if conf < LOW_CONF:
            flags.append("待核")
        if len(text) < 10:
            flags.append("题面过短，疑似边界")
            suspect += 1
        if not points:
            flags.append("缺分值")
        kind = infer_type(text)
        if kind == "未定":
            flags.append("题型待定")
        if pages:
            start, end = min(pages), max(pages)
        else:
            start = end = 1
            if current["lines"] and current["lines"][0]["page"] is None:
                flags.append("页码待核")
        row = {
            "paper": current["paper"],
            "no": str(current["no"]),
            "page_start": start,
            "page_end": end,
            "stem": text,
            "type": kind,
            "source": current["source"],
            "conf": conf,
            "flags": flags,
        }
        if points:
            row["points"] = float(points.group(1))
        if current["sub"]:
            row["sub"] = current["sub"]
        if section:
            row["section"] = section
        questions.append(row)
        current = None

    for line in lines:
        text = line["text"]
        head = MD_Q.match(text) or Q_START.match(text) or Q_ALT.match(text)
        sub = SUB.match(text)
        if head:
            flush()
            current = {"no": int(head.group(1)), "lines": [line], "sub": [],
                       "paper": "", "source": ""}
            continue
        if SUB.match(text):
            if current is None:
                current = {"no": int(sub.group(1)), "lines": [line], "sub": [],
                           "paper": "", "source": ""}
                warnings.append(f"子题（{sub.group(1)}）前没有大题号，按独立题处理")
            else:
                current["sub"].append(int(sub.group(1)))
                current["lines"].append(line)
            continue
        if SECTION.match(text):
            flush()
            section = text.strip()
            continue
        if current is not None:
            current["lines"].append(line)
    flush()
    if suspect:
        warnings.append(f"{suspect} 题题面短于 10 字，疑似切题边界，请人工确认后再入库")
    return questions, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="文本/Markdown，或 ocr_page.py --json 的输出")
    parser.add_argument("--paper", help="卷别（默认取文件名）")
    parser.add_argument("--out", help="写入 JSONL 草稿（默认打印到标准输出）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    try:
        lines, warnings = read_lines(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"无法读取输入（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 2

    source = "ocr" if path.suffix.lower() == ".json" else "text-layer"
    questions, split_warnings = split(lines)
    warnings.extend(split_warnings)
    paper = args.paper or path.stem
    for row in questions:
        row["paper"] = paper
        row["source"] = source

    if not questions:
        print(f"未识别到题号，请检查切题规则：{path}", file=sys.stderr)
        return 1

    summary = {
        "paper": paper,
        "source": source,
        "questions": len(questions),
        "flags": {},
        "missing": {
            "points": sum(1 for row in questions if "points" not in row),
            "type": sum(1 for row in questions if row["type"] == "未定"),
        },
        "warnings": warnings,
    }
    for row in questions:
        for flag in row["flags"]:
            summary["flags"][flag] = summary["flags"].get(flag, 0) + 1

    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in questions)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {target} questions={len(questions)}")
    elif args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"paper={paper} source={source} questions={len(questions)}")
        for row in questions:
            points = row.get("points")
            mark = f"{points:g}分 {row['type']} conf={row['conf']:.2f}" if points else \
                f"?分 {row['type']} conf={row['conf']:.2f}"
            print(f"#{row['no']} p{row['page_start']}-{row['page_end']} [{mark}] "
                  f"{row['stem'][:60]}")
        for item in warnings:
            print(f"warn={item}")
        missing = summary["missing"]
        if missing["points"] or missing["type"]:
            print(f"hint=草稿缺字段：points {missing['points']} 题、type {missing['type']} 题；"
                  "补齐后再跑 intake_check.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
