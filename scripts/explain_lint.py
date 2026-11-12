#!/usr/bin/env python3
"""Lint a worked-solution note or a follow-up explanation against the fixed flow.

solution (seven parts, all required):
  题目 / 定性 / 完整解答 / 考场写法 / 验算与边界 / 易错点 / 一句话总结
  - 易错点 at least 3 numbered items
  - 验算与边界 has a table or a special-value check (numeric policy)

followup (five parts):
  问题 / 一句话结论 / at least two 视角 (or 推导/证明) /
  verification heading with a table / one comparison table / closing 一句话总结

multi (multi-solution note):
  题目 / 常规解法 / 多解对比 (table with >=2 solutions and a 适用 column) /
  验算 / 来源 (URL + 访问 date, or 未外部检索) / 一句话总结

Subject policy: `--verify numeric|evidence|any` decides what counts as a
verification section (numeric: 数值/验算/特殊值; evidence: 证据/核对/评分点).
`--subject calculus` reads the `verify:` field from
subjects/<subject>/verification.md; an explicit --verify wins.

Usage:
  python explain_lint.py note.md
  python explain_lint.py note.md --level solution --require-frontmatter --json
  python explain_lint.py note.md --level followup --subject calculus
  python explain_lint.py note.md --level multi --verify evidence

Exit codes: 0 pass; 1 missing parts or not deep enough; 2 unreadable file or
unknown subject policy.
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
    ("验算与边界", ("验算", "验证", "边界", "特殊事实", "证据", "核对")),
    ("易错点", ("易错点", "常见错误")),
    ("一句话总结", ("一句话", "总结")),
]
FOLLOWUP_VIEWS = ("视角", "推导", "证明")
BANNED = ("显然", "易得")
NUMERIC_WORDS = ("数值", "验算", "特殊值")
EVIDENCE_WORDS = ("证据", "核对", "评分点")
VERIFY_WORDS = NUMERIC_WORDS + EVIDENCE_WORDS + ("验证",)
NUMERIC_MARKS = re.compile(r"(≈|代入|取点|端点)")


def verify_head_words(policy: str) -> tuple[str, ...]:
    if policy == "numeric":
        return NUMERIC_WORDS
    if policy == "evidence":
        return EVIDENCE_WORDS
    return VERIFY_WORDS


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


def section_title(text: str, aliases: tuple[str, ...]) -> str:
    for _, title in headings(text):
        if any(alias in title for alias in aliases):
            return title
    return ""


def policy_title_ok(policy: str, title: str) -> bool:
    """numeric 段标题要体现数值/验算，evidence 段标题要体现证据/评分点。"""
    if policy == "numeric":
        return any(word in title for word in NUMERIC_WORDS)
    if policy == "evidence":
        return any(word in title for word in EVIDENCE_WORDS)
    return True


def section_body(text: str, aliases: tuple[str, ...]) -> str:
    """Section content without its heading line."""
    body = section(text, aliases)
    return "\n".join(body.splitlines()[1:]).strip()


def count_items(body: str) -> int:
    return len(re.findall(r"^\s*(?:\d+[.、)]|[①②③④⑤⑥⑦⑧⑨⑩])", body, re.M))


def table_rows(text: str) -> int:
    return len(re.findall(r"^\s*\|.*\|\s*$", text, re.M))


def table_data_rows(text: str) -> int:
    """表格数据行数：跳过表头与分隔行，只数真正的内容行。"""
    count = 0
    in_body = False
    for line in text.splitlines():
        if not re.match(r"^\s*\|.*\|\s*$", line):
            in_body = False
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if any(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            in_body = True
            continue
        if in_body and any(cells):
            count += 1
    return count


def frontmatter_missing(text: str) -> list[str]:
    if not text.startswith("---"):
        return ["frontmatter"]
    end = text.find("\n---", 3)
    if end < 0:
        return ["frontmatter"]
    block = text[3:end]
    return [f"frontmatter:{key}" for key in ("id", "type", "source", "status", "confidence")
            if not re.search(rf"^{key}\s*:", block, re.M)]


def check_solution(text: str, verify: str = "any") -> dict:
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
            if not policy_title_ok(verify, section_title(text, aliases)):
                missing.append(f"验证段标题不符合 {verify} 策略")
                continue
            rows = table_rows(body)
            stats["验算表格行数"] = rows
            if verify == "evidence":
                if rows < 1:
                    missing.append("验证与边界：缺证据/评分点表格")
            elif rows < 1 and not NUMERIC_MARKS.search(body):
                missing.append("验算与边界：缺表格或特殊值检查")
        elif name == "一句话总结":
            if len(body.strip()) < 10:
                missing.append("一句话总结过短")

    return {"ok": not missing, "missing": missing, "stats": stats}


def check_followup(text: str, verify: str = "any") -> dict:
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

    words = verify_head_words(verify)
    verify_titles = [title for _, title in items
                     if any(word in title for word in words)]
    stats["验证段数"] = len(verify_titles)
    stats["表格行数"] = table_rows(text)
    stats["验证段数据行"] = max(
        (table_data_rows(section_body(text, (title,))) for title in verify_titles),
        default=0,
    )
    if not verify_titles:
        missing.append("验证（标题 + 表格）")
    elif stats["验证段数据行"] < 1:
        missing.append("验证段缺表格数据行")
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


def check_multi(text: str, verify: str = "any") -> dict:
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

    check = section(text, ("验算", "验证", "特殊值", "证据", "核对"))
    if not check:
        missing.append("验算")
    elif not policy_title_ok(verify, section_title(text, ("验算", "验证", "特殊值", "证据", "核对"))):
        missing.append(f"验证段标题不符合 {verify} 策略")
    elif verify == "evidence":
        if table_rows(check) < 1:
            missing.append("验证：缺证据/评分点表格")
    elif table_rows(check) < 1 and not NUMERIC_MARKS.search(check):
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


def run(text: str, level: str, require_frontmatter: bool,
        verify: str = "any") -> dict:
    levels: dict[str, dict] = {}
    if level in ("solution", "both"):
        levels["solution"] = check_solution(text, verify)
    if level in ("followup", "both"):
        levels["followup"] = check_followup(text, verify)
    if level == "multi":
        levels["multi"] = check_multi(text, verify)

    missing = [item for name, result in levels.items()
               for item in result["missing"]]
    if require_frontmatter:
        missing.extend(frontmatter_missing(text))

    warnings: list[str] = []
    hits = sum(text.count(word) for word in BANNED)
    if hits:
        warnings.append(f"出现「显然/易得」共 {hits} 处；关键步骤要写依据")

    return {"level": level, "verify": verify, "ok": not missing, "missing": missing,
            "warnings": warnings, "levels": levels}


def detect(text: str) -> str:
    follow = bool(re.search(r"^#{1,4}\s*追问", text, re.M))
    core = any(any(alias in title for alias in ("定性", "完整解答", "易错点"))
               for _, title in headings(text))
    if core:
        return "solution"  # 讲题稿即使附「追问」附录，仍按更严的七段检查
    return "followup" if follow else "solution"


def subject_policy(skill_root: Path, subject: str) -> str:
    """读 subjects/<subject>/verification.md frontmatter 的 verify 字段。"""
    path = skill_root / "subjects" / subject / "verification.md"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"读不到学科验证协议 {path}：{exc}", file=sys.stderr)
        raise SystemExit(2)
    if not text.startswith("---"):
        print(f"{path} 缺 frontmatter（需 subject/verify）", file=sys.stderr)
        raise SystemExit(2)
    end = text.find("\n---", 3)
    block = text[3:end] if end >= 0 else text[3:]
    match = re.search(r"^verify\s*:\s*(\S+)\s*$", block, re.M)
    policy = match.group(1) if match else ""
    if policy not in ("any", "numeric", "evidence"):
        print(f"{path} 的 verify 必须是 any/numeric/evidence，当前：{policy or '缺失'}",
              file=sys.stderr)
        raise SystemExit(2)
    return policy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Markdown 讲题稿或追问稿")
    parser.add_argument("--level", choices=["auto", "solution", "followup", "multi", "both"],
                        default="auto")
    parser.add_argument("--verify", choices=["any", "numeric", "evidence"], default=None,
                        help="验证类型；显式指定时覆盖 --subject")
    parser.add_argument("--subject", help="学科包名，读取 subjects/<name>/verification.md 的 verify")
    parser.add_argument("--skill-root", default=str(Path(__file__).resolve().parents[1]),
                        help="学科包根目录，默认取脚本所在技能根")
    parser.add_argument("--require-frontmatter", action="store_true",
                        help="notes/*.md 成稿必须带 id/type/source/status/confidence")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if args.verify:
        verify = args.verify
    elif args.subject:
        verify = subject_policy(Path(args.skill_root), args.subject)
    else:
        verify = "any"

    path = Path(args.path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"无法读取 {path}：{exc}", file=sys.stderr)
        return 2

    level = detect(text) if args.level == "auto" else args.level
    result = run(text, level, args.require_frontmatter, verify)
    result["path"] = str(path)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        parts = ", ".join(f"{name}={len(data['missing'])} 缺"
                          for name, data in result["levels"].items())
        print(("PASS" if result["ok"] else "FAIL") + f" {path.name} [{level}/{verify}] {parts}")
        for item in result["missing"]:
            print(f"  缺：{item}")
        for item in result["warnings"]:
            print(f"  提醒：{item}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
