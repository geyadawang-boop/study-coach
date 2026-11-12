#!/usr/bin/env python3
"""Lint a worked-solution note or a follow-up explanation against the fixed flow.

solution (seven parts, all required):
  题目 / 定性 / 完整解答 / 考场写法 / 验算与边界 / 易错点 / 一句话总结
  - 易错点 at least 3 numbered items
  - 验算与边界 has a table or a special-value check

followup (five parts):
  问题 / 一句话结论 / at least two 视角 (or 推导/证明) /
  verification heading (数值/验算/特殊值 for math, or 证据/核对/评分点 for other subjects)
  with a table / one comparison table / closing 一句话总结

multi (multi-solution note):
  题目 / 常规解法 / 多解对比 (table with >=2 solutions and a 适用 column) /
  验算 / 来源 (URL + 访问 date, or 未外部检索) / 一句话总结

Usage:
  python explain_lint.py note.md
  python explain_lint.py note.md --level solution --require-frontmatter --json
  python explain_lint.py note.md --level followup
  python explain_lint.py note.md --level multi

Exit codes: 0 pass; 1 missing parts or not deep enough; 2 unreadable file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SOLUTION = [
    ("题目", ("题目", "题面")),
    ("定性", ("定性", "思路")),
    ("完整解答", ("完整解答", "解答", "步骤")),
    ("考场写法", ("考场写法", "考场精简", "可提交")),
    ("验算与边界", ("验算", "边界", "特殊事实")),
    ("易错点", ("易错点", "常见错误")),
    ("一句话总结", ("一句话", "总结")),
]
FOLLOWUP_VIEWS = ("视角", "推导", "证明")
BANNED = ("显然", "易得")


def headings(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(1).strip())
            for m in re.finditer(r"^#{2,4}\s+(.+)$", text, re.M)]


def has_bold(text: str, word: str) -> bool:
    return bool(re.search(r"^\*\*[^\n]*" + re.escape(word) + r"[^\n]*\*\*\s*$",
                          text, re.M))


def section(text: str, aliases: tuple[str, ...]) -> str:
    items = headings(text)
    for index, (pos, title) in enumerate(items):
        if any(alias in title for alias in aliases):
            end = items[index + 1][0] if index + 1 < len(items) else len(text)
            return text[pos:end]
    return ""


def section_body(text: str, aliases: tuple[str, ...]) -> str:
    """Section content without its heading line."""
    body = section(text, aliases)
    return "\n".join(body.splitlines()[1:]).strip()


def count_items(body: str) -> int:
    return len(re.findall(r"^\s*(?:\d+[.、)]|[①②③④⑤⑥⑦⑧⑨⑩])", body, re.M))


def table_rows(text: str) -> int:
    return len(re.findall(r"^\s*\|.*\|\s*$", text, re.M))


def frontmatter_missing(text: str) -> list[str]:
    if not text.startswith("---"):
        return ["frontmatter"]
    end = text.find("\n---", 3)
    if end < 0:
        return ["frontmatter"]
    block = text[3:end]
    return [f"frontmatter:{key}" for key in ("id", "type", "source", "status", "confidence")
            if not re.search(rf"^{key}\s*:", block, re.M)]


def check_solution(text: str) -> dict:
    missing: list[str] = []
    stats: dict[str, int] = {}

    if not has_bold(text, "题目") and not section(text, SOLUTION[0][1]):
        missing.append("题目")

    for name, aliases in SOLUTION[1:]:
        body = section(text, aliases)
        if not body:
            missing.append(name)
            continue
        if name == "易错点":
            stats["易错点条数"] = count_items(body)
            if stats["易错点条数"] < 3:
                missing.append("易错点至少 3 条")
        elif name == "验算与边界":
            rows = table_rows(body)
            stats["验算表格行数"] = rows
            if rows < 1 and not re.search(r"(≈|代入|取点|端点)", body):
                missing.append("验算与边界：缺表格或特殊值检查")
        elif name == "一句话总结":
            if len(body.strip()) < 10:
                missing.append("一句话总结过短")

    return {"ok": not missing, "missing": missing, "stats": stats}


def check_followup(text: str) -> dict:
    missing: list[str] = []
    stats: dict[str, int] = {}
    items = headings(text)

    if not (has_bold(text, "问题") or any("问题" in t for _, t in items)
            or "追问" in text[:400]):
        missing.append("问题复述")

    views = [t for _, t in items if any(word in t for word in FOLLOWUP_VIEWS)]
    stats["视角数"] = len(views)
    if len(views) < 2:
        missing.append("至少两种视角")

    verify_head = any(any(word in t for word in ("数值", "验算", "特殊值",
                                                  "证据", "核对", "评分点", "验证"))
                      for _, t in items)
    stats["表格行数"] = table_rows(text)
    if not (verify_head and stats["表格行数"] >= 1):
        missing.append("验证（标题 + 表格）")
    if stats["表格行数"] < 1:
        missing.append("对比表")

    summary_titles = [title for _, title in items if "一句话" in title or "总结" in title]
    if not summary_titles:
        missing.append("一句话总结")
    else:
        stats["结论字数"] = max(len(section_body(text, (title,))) for title in summary_titles)
        if stats["结论字数"] < 10:
            missing.append("一句话结论过短")
        if "一句话" not in items[-1][1] and "总结" not in items[-1][1]:
            missing.append("结尾一句话总结")

    return {"ok": not missing, "missing": missing, "stats": stats}


def check_multi(text: str) -> dict:
    missing: list[str] = []
    stats: dict[str, int] = {}

    if not has_bold(text, "题目") and not section(text, ("题目", "题面")):
        missing.append("题目")

    standard = section(text, ("常规解法", "通法", "标准解法"))
    if not standard:
        missing.append("常规解法")
    elif len(standard.strip()) < 30:
        missing.append("常规解法过短")

    compare = section(text, ("多解对比", "一题多解", "解法对比"))
    if not compare:
        missing.append("多解对比")
    else:
        rows = table_rows(compare)
        stats["对比表行数"] = rows
        if rows < 4:
            missing.append("多解对比表至少 2 种解法")
        header = next((line for line in compare.splitlines()
                       if line.strip().startswith("|")), "")
        for word in ("解法", "适用"):
            if word not in header:
                missing.append(f"多解对比表缺「{word}」列")
        if not any(word in compare for word in ("常规", "通法", "标准")):
            missing.append("多解对比未标出常规解法")
        if not any(word in compare for word in ("巧解", "另解", "解法二", "第二", "替代")):
            missing.append("多解对比未给替代解法")

    check = section(text, ("验算", "验证", "特殊值"))
    if not check:
        missing.append("验算")
    elif table_rows(check) < 1 and not re.search(r"(≈|代入|取点|端点)", check):
        missing.append("验算：缺表格或特殊值检查")

    source = section(text, ("来源", "检索来源", "参考来源"))
    if not source:
        missing.append("来源")
    else:
        has_url = bool(re.search(r"https?://", source))
        if not has_url and "未外部检索" not in source:
            missing.append("来源：缺链接或「未外部检索」声明")
        if has_url and "访问" not in source:
            missing.append("来源：链接缺访问日期")

    if not any("一句话" in t or "总结" in t for _, t in headings(text)):
        missing.append("一句话总结")

    return {"ok": not missing, "missing": missing, "stats": stats}


def run(text: str, level: str, require_frontmatter: bool) -> dict:
    levels: dict[str, dict] = {}
    if level in ("solution", "both"):
        levels["solution"] = check_solution(text)
    if level in ("followup", "both"):
        levels["followup"] = check_followup(text)
    if level == "multi":
        levels["multi"] = check_multi(text)

    missing = [item for name, result in levels.items()
               for item in result["missing"]]
    if require_frontmatter:
        missing.extend(frontmatter_missing(text))

    warnings: list[str] = []
    hits = sum(text.count(word) for word in BANNED)
    if hits:
        warnings.append(f"出现「显然/易得」共 {hits} 处；关键步骤要写依据")

    return {"level": level, "ok": not missing, "missing": missing,
            "warnings": warnings, "levels": levels}


def detect(text: str) -> str:
    follow = bool(re.search(r"^#{1,4}\s*追问", text, re.M))
    core = any(any(alias in title for alias in ("定性", "完整解答", "易错点"))
               for _, title in headings(text))
    if core:
        return "solution"  # 讲题稿即使附「追问」附录，仍按更严的七段检查
    return "followup" if follow else "solution"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Markdown 讲题稿或追问稿")
    parser.add_argument("--level", choices=["auto", "solution", "followup", "multi", "both"],
                        default="auto")
    parser.add_argument("--require-frontmatter", action="store_true",
                        help="notes/*.md 成稿必须带 id/type/source/status/confidence")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    path = Path(args.path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"无法读取 {path}：{exc}", file=sys.stderr)
        return 2

    level = detect(text) if args.level == "auto" else args.level
    result = run(text, level, args.require_frontmatter)
    result["path"] = str(path)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        parts = ", ".join(f"{name}={len(data['missing'])} 缺"
                          for name, data in result["levels"].items())
        print(("PASS" if result["ok"] else "FAIL") + f" {path.name} [{level}] {parts}")
        for item in result["missing"]:
            print(f"  缺：{item}")
        for item in result["warnings"]:
            print(f"  提醒：{item}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
