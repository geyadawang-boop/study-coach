#!/usr/bin/env python3
"""Quality gate for an imported exam paper.

Two input forms:

1. A Markdown paper whose questions are headings such as
   `## 第 3 题（8 分）` or `## 3.（8分）`, with a declared total like
   `满分：150分`. Questions, points and total are cross-checked.
2. A JSONL file following the unified question schema in
   `references/paper-intake.md`. Schema fields, duplicates, confidence,
   page range, field types, the point sum and the question-type mix are
   checked.

Required JSONL fields: paper, no, stem, type, points, source, conf,
page_start. Optional: page_end (defaults to page_start), latex, figure,
flags, aliases. `latex`/`flags`/`aliases` must be lists when present,
`figure` must be a string.

Exit code 0 = gate passed, 1 = errors found, 2 = cannot read input.
This is the regression gate for paper splitting: fixtures live in
`evals/fixtures/`.

Usage:
  python intake_check.py paper.md [--expect-count 20] [--expect-total 150]
  python intake_check.py questions.jsonl --json
  python intake_check.py questions.jsonl --expect-types 计算:2,证明:1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^#{1,6}\s*(?:第\s*)?(\d+)\s*(?:题|[.、．])\s*(.*)$")
POINTS = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]")
TOTAL = re.compile(r"(?:满分|总分)\s*[：:]\s*(\d+(?:\.\d+)?)\s*分")
REQUIRED = ("paper", "no", "stem", "type", "points", "source", "conf", "page_start")


def finding(level: str, msg: str) -> dict:
    return {"level": level, "msg": msg}


def check_markdown(path: Path) -> tuple[list[dict], dict]:
    out: list[dict] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    declared = None
    match = TOTAL.search(raw)
    if match:
        declared = float(match.group(1))

    questions: list[tuple[int, float | None, str]] = []
    current: list[str] = []
    current_no = None
    current_points = None

    def flush() -> None:
        nonlocal current, current_no, current_points
        stem = "\n".join(current).strip()
        if current_no is not None:
            questions.append((current_no, current_points, stem))
        current = []

    for line in raw.splitlines():
        head = HEADING.match(line)
        if head:
            flush()
            current_no = int(head.group(1))
            rest = head.group(2)
            points = POINTS.search(line)
            current_points = float(points.group(1)) if points else None
            current = [rest.strip()] if rest.strip() else []
            continue
        if current_no is not None:
            current.append(line)
    flush()

    if not questions:
        out.append(finding("error", "未识别到任何题目标题（需形如「## 第 3 题（8 分）」）"))
        return out, {"form": "markdown", "count": 0, "points": 0, "declared": declared}

    numbers = [no for no, _, _ in questions]
    duplicates = sorted({no for no in numbers if numbers.count(no) > 1})
    if duplicates:
        out.append(finding("error", f"题号重复：{duplicates}"))
    missing = [str(no) for no, points, _ in questions if points is None]
    if missing:
        out.append(finding("error", f"题号 {missing} 缺分值，无法核对总分"))
    short = [str(no) for no, _, stem in questions if len(stem) < 10]
    if short:
        out.append(finding("warn", f"题号 {short} 题面短于 10 字，可能切题或抽取不完整"))

    total = sum(points or 0 for _, points, _ in questions)
    if declared is None:
        out.append(finding("warn", "卷面未声明满分，无法核对总分；请补「满分：N分」"))
    elif abs(total - declared) > 1e-6:
        out.append(finding("error", f"各题分值合计 {total:g} 与卷面总分 {declared:g} 不一致"))

    return out, {
        "form": "markdown",
        "count": len(questions),
        "points": total,
        "declared": declared,
        "types": None,
    }


def check_jsonl(path: Path) -> tuple[list[dict], dict]:
    out: list[dict] = []
    rows: list[dict] = []
    for no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            out.append(finding("error", f"第 {no} 行无法解析：{exc.msg}"))
            continue
        if not isinstance(item, dict):
            out.append(finding("error", f"第 {no} 行不是对象"))
            continue
        missing = [key for key in REQUIRED if item.get(key) in (None, "", [])]
        if missing:
            out.append(finding("error", f"第 {no} 行缺字段：{missing}"))
        try:
            points = float(item.get("points"))
        except (TypeError, ValueError):
            points = None
            out.append(finding("error", f"第 {no} 行 points 不是数字"))
        try:
            conf = float(item.get("conf"))
        except (TypeError, ValueError):
            conf = None
            out.append(finding("error", f"第 {no} 行 conf 不是数字"))
        flags = item.get("flags") or []
        if conf is not None and conf < 0.90 and "待核" not in "".join(map(str, flags)):
            out.append(finding("error", f"第 {no} 行 conf={conf:.2f} < 0.90 但未标「待核」"))

        page_start = item.get("page_start")
        page_end = item.get("page_end", page_start)
        if page_start not in (None, ""):
            if not isinstance(page_start, int) or isinstance(page_start, bool) or page_start < 1:
                out.append(finding("error", f"第 {no} 行 page_start 必须是 ≥1 的整数"))
            elif not isinstance(page_end, int) or isinstance(page_end, bool) or page_end < page_start:
                out.append(finding("error",
                                   f"第 {no} 行 page_end={page_end} 小于 page_start={page_start}"))
        for key in ("latex", "flags", "aliases"):
            if key in item and item[key] is not None and not isinstance(item[key], list):
                out.append(finding("error", f"第 {no} 行 {key} 必须是数组"))
        if item.get("figure") not in (None, "") and not isinstance(item.get("figure"), str):
            out.append(finding("error", f"第 {no} 行 figure 必须是图片路径字符串"))

        if str(item.get("stem") or "").strip() and len(str(item["stem"]).strip()) < 10:
            out.append(finding("warn", f"第 {no} 行题面短于 10 字，可能抽取不完整"))
        item["_points"] = points
        rows.append(item)

    keys = [(str(row.get("paper") or ""), str(row.get("no") or "")) for row in rows]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        out.append(finding("error", f"(paper, no) 重复：{duplicates}"))

    types: dict[str, int] = {}
    for row in rows:
        name = str(row.get("type") or "").strip() or "未标"
        types[name] = types.get(name, 0) + 1

    total = sum(row["_points"] or 0 for row in rows)
    return out, {"form": "jsonl", "count": len(rows), "points": total,
                 "declared": None, "types": types}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Markdown 试卷或题目 JSONL")
    parser.add_argument("--expect-count", type=int, help="期望题数（回归集用）")
    parser.add_argument("--expect-total", type=float, help="期望总分（回归集用）")
    parser.add_argument("--expect-types", help="期望题型分布，如「计算:2,证明:1」（仅 JSONL）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.is_file():
        print(f"not a file: {path}", file=sys.stderr)
        return 2
    if path.suffix.lower() == ".jsonl":
        findings, summary = check_jsonl(path)
    else:
        findings, summary = check_markdown(path)

    if args.expect_count is not None and summary["count"] != args.expect_count:
        findings.append(finding(
            "error", f"题数 {summary['count']} 与期望 {args.expect_count} 不一致"
        ))
    if args.expect_total is not None and abs(summary["points"] - args.expect_total) > 1e-6:
        findings.append(finding(
            "error", f"总分 {summary['points']:g} 与期望 {args.expect_total:g} 不一致"
        ))
    if args.expect_types:
        if summary["types"] is None:
            findings.append(finding(
                "error", "Markdown 卷没有 type 字段，无法核对题型分布；改用 JSONL 或去掉 --expect-types"
            ))
        else:
            expected: dict[str, int] = {}
            malformed = False
            for part in args.expect_types.replace("，", ",").split(","):
                part = part.strip()
                if not part:
                    continue
                name, sep, count = part.replace("：", ":").partition(":")
                if not sep or not name.strip():
                    malformed = True
                    break
                try:
                    expected[name.strip()] = int(count)
                except ValueError:
                    malformed = True
                    break
            if malformed or not expected:
                findings.append(finding(
                    "error", f"--expect-types 格式应为「题型:数量,...」，收到：{args.expect_types}"
                ))
            elif expected != summary["types"]:
                findings.append(finding(
                    "error", f"题型分布 {summary['types']} 与期望 {expected} 不一致"
                ))

    errors = [f for f in findings if f["level"] == "error"]
    if args.json:
        print(json.dumps(
            {"path": str(path), "summary": summary, "findings": findings,
             "ok": not errors},
            ensure_ascii=False, indent=2,
        ))
    else:
        type_text = (
            ",".join(f"{name}:{count}" for name, count in sorted(summary["types"].items()))
            if summary["types"] else "-"
        )
        print(
            f"form={summary['form']} questions={summary['count']} "
            f"points={summary['points']:g} declared={summary['declared']} types={type_text}"
        )
        for item in findings:
            print(f"[{'ERROR' if item['level'] == 'error' else 'WARN'}] {item['msg']}")
        print(f"gate={'PASS' if not errors else 'FAIL'}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
