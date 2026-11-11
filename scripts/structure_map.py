#!/usr/bin/env python3
"""Render 01-知识地图.md as a navigation map, not a chapter list.

Borrowed from the 结构地图 SOP in the open study toolkit:

- group by priority (必掌握 / 高频 / 了解), never by textbook order;
- draw only prerequisite edges, and keep their count bounded;
- mark an explicit "unknown" region instead of pretending completeness;
- flag cycles and unresolved prerequisites.

Output: views/结构地图.md (mermaid mindmap + dependency table + unknowns).

Usage:
  python structure_map.py --kb 学习库
  python structure_map.py --kb 学习库 --strict --json

Exit codes: 0 = rendered (warnings allowed); 1 = cycle found, or --strict
threshold violation; 2 = missing knowledge base or empty map.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TIERS = ("必掌握", "高频", "了解")
NO_PREREQ = {"无", "-", "—", "–", "none", "None", ""}
# 「0/0 型极限」「a/b」里的斜杠是术语的一部分，不能当列表分隔符。
# 只在两侧都是汉字时才把 / 视为并列分隔（如「极限/连续」）。
SPLIT_PREREQ = re.compile(r"[；;、,，]+|(?<=[\u4e00-\u9fff])/(?=[\u4e00-\u9fff])")
MERMAID_BAD = re.compile(r"[()\[\]{}]")
EXAMPLE_PREFIX = re.compile(r"^示例[:：]\s*")
MUST_RATIO = 0.4
EDGE_RATIO = 1.5
MIN_NODES_FOR_RATIO = 5


def clean_name(value: str) -> str:
    text = EXAMPLE_PREFIX.sub("", value.strip())
    return text.strip()


def priority_for(requirement: str) -> str:
    text = requirement.strip()
    if re.search(r"(不要求|不需要|无需|不必|不须|无须)\s*掌握", text):
        return "了解"  # 「了解，不要求掌握」不是必掌握
    if "掌握" in text:
        return "必掌握"
    if "高频" in text:
        return "高频"
    if "理解" in text or "熟悉" in text:
        return "高频"
    if "了解" in text or "知道" in text:
        return "了解"
    return "了解"


def parse_map(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.strip()
        if not text.startswith("|"):
            continue
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        if len(cells) < 4:
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
            continue
        if cells[0] in {"知识点", ""}:
            continue
        name = clean_name(cells[0])
        if not name:
            continue
        raw_pre = cells[2] if len(cells) > 2 else ""
        prereq = []
        if raw_pre not in NO_PREREQ:
            prereq = [clean_name(part) for part in SPLIT_PREREQ.split(raw_pre) if clean_name(part)]
        rows.append({
            "name": name,
            "requirement": cells[1] if len(cells) > 1 else "",
            "prerequisites": [p for p in prereq if p not in NO_PREREQ],
            "status": cells[3] if len(cells) > 3 else "",
            "evidence": cells[4] if len(cells) > 4 else "-",
            "tier": priority_for(cells[1] if len(cells) > 1 else ""),
        })
    return rows


def canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """Rotate a cycle to its smallest node so the same loop is reported once."""
    body = cycle[:-1] if len(cycle) > 1 and cycle[0] == cycle[-1] else cycle
    if not body:
        return tuple(cycle)
    start = min(range(len(body)), key=lambda i: body[i])
    return tuple(body[start:] + body[:start])


def find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Iterative DFS: a deep prerequisite chain must not hit RecursionError."""
    cycles: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    state: dict[str, int] = {node: 0 for node in graph}
    for start in graph:
        if state[start] != 0:
            continue
        state[start] = 1
        path = [start]
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, index = stack[-1]
            edges = graph.get(node, [])
            if index >= len(edges):
                state[node] = 2
                stack.pop()
                path.pop()
                continue
            nxt = edges[index]
            stack[-1] = (node, index + 1)
            if state.get(nxt, 0) == 0:
                state[nxt] = 1
                path.append(nxt)
                stack.append((nxt, 0))
            elif state.get(nxt) == 1:
                pos = path.index(nxt)
                cycle = path[pos:] + [nxt]
                key = canonical_cycle(cycle)
                if key not in seen:
                    seen.add(key)
                    cycles.append(cycle)
    return cycles


def merge_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Merge duplicate knowledge points instead of letting the last row win.

    A duplicate row used to overwrite the earlier row's prerequisites, which
    could silently hide a real cycle. Merge prerequisites and report the name.
    """
    merged: dict[str, dict] = {}
    duplicates: list[str] = []
    for row in rows:
        name = row["name"]
        if name in merged:
            duplicates.append(name)
            for pre in row["prerequisites"]:
                if pre not in merged[name]["prerequisites"]:
                    merged[name]["prerequisites"].append(pre)
            continue
        merged[name] = row
    return list(merged.values()), list(dict.fromkeys(duplicates))


def mermaid_text(value: str) -> str:
    return MERMAID_BAD.sub("/", value).replace('"', "'")


def build(kb: Path) -> tuple[str, dict]:
    map_path = kb / "01-知识地图.md"
    rows, duplicates = merge_rows(parse_map(map_path))
    names = {row["name"] for row in rows}
    graph = {row["name"]: [p for p in row["prerequisites"] if p in names] for row in rows}

    unresolved: list[tuple[str, str]] = []
    for row in rows:
        for pre in row["prerequisites"]:
            if pre not in names:
                unresolved.append((row["name"], pre))

    cycles = find_cycles(graph)
    tiers: dict[str, list[str]] = {tier: [] for tier in TIERS}
    for row in rows:
        tiers[row["tier"]].append(row["name"])
    unknown_nodes = [
        row["name"] for row in rows
        if row["status"].strip() in {"未讲", "待补", "", "-"}
    ]
    no_evidence = [
        row["name"] for row in rows
        if row["evidence"].strip() in {"", "-", "—"}
    ]

    used_tiers = [tier for tier in TIERS if tiers[tier]]
    edge_count = sum(len(v) for v in graph.values())
    must_count = len(tiers["必掌握"])
    must_ratio = must_count / len(rows) if rows else 0.0
    violations: list[str] = []
    if len(rows) >= MIN_NODES_FOR_RATIO:
        if must_ratio > MUST_RATIO:
            violations.append(f"必掌握占比 {must_ratio:.0%}，超过四成，等于没做优先级判断")
        if not 3 <= len(used_tiers) <= 7:
            violations.append(f"一级分支 {len(used_tiers)} 个，应在 3–7 之间")
        if edge_count > EDGE_RATIO * len(rows):
            violations.append(f"依赖边 {edge_count} 条，超过叶子的 1.5 倍（{EDGE_RATIO * len(rows):.1f}）")
    for cycle in cycles:
        violations.append("前置成环：" + " → ".join(cycle))
    for name in duplicates:
        violations.append(f"知识点重复出现：{name}（前置已合并）")

    lines = [
        "<!-- generated by scripts/structure_map.py; do not edit by hand -->",
        "",
        "# 结构地图",
        "",
        "> 由 `scripts/structure_map.py` 从 `01-知识地图.md` 生成，不手改。",
        "> 按优先级分组，不按教材章节顺序；依赖边只画前置关系。",
        "",
        "```mermaid",
        "mindmap",
        "  root((知识地图))",
    ]
    for tier in used_tiers:
        lines.append(f"    {tier}")
        for name in tiers[tier]:
            lines.append(f"      {mermaid_text(name)}")
    if unresolved or unknown_nodes:
        lines.append("    未知")
        for owner, pre in unresolved:
            lines.append(f"      前置未登记：{mermaid_text(pre)}（{mermaid_text(owner)}）")
        for name in unknown_nodes[:10]:
            lines.append(f"      未讲或待补：{mermaid_text(name)}")
    lines += [
        "```",
        "",
        "## 重难点清单",
        "",
        "| 档位 | 数量 | 知识点 |",
        "| --- | --- | --- |",
    ]
    for tier in TIERS:
        lines.append(f"| {tier} | {len(tiers[tier])} | {'、'.join(tiers[tier]) or '-'} |")

    lines += ["", "## 依赖关系", ""]
    if edge_count:
        lines += ["| 先学 | 后学 |", "| --- | --- |"]
        for row in rows:
            for pre in row["prerequisites"]:
                if pre in names:
                    lines.append(f"| {pre} | {row['name']} |")
    else:
        lines.append("- 暂无前置关系；如果知识点之间确有依赖，补进 `01-知识地图.md` 的前置列。")

    lines += ["", "## 未知区域", ""]
    if unresolved:
        lines.append("- 前置未登记：" + "；".join(f"{owner} ← {pre}" for owner, pre in unresolved))
    if unknown_nodes:
        lines.append("- 未讲或待补：" + "、".join(unknown_nodes))
    if no_evidence:
        lines.append("- 无证据：" + "、".join(no_evidence))
    if not (unresolved or unknown_nodes or no_evidence):
        lines.append("- 无；地图范围内没有发现缺口。")

    lines += [
        "",
        "## 自检",
        "",
        f"- 知识点 {len(rows)} 个，依赖边 {edge_count} 条。",
        f"- 必掌握 {must_count} 个，占 {must_ratio:.0%}。",
        f"- 一级分支 {len(used_tiers)} 个。",
    ]
    if violations:
        lines.append("- 未过门槛：")
        lines.extend(f"  - {item}" for item in violations)
    else:
        lines.append("- 门槛通过：优先级有区分、依赖边数量受控、无环。")

    summary = {
        "kb": kb.as_posix(),
        "nodes": len(rows),
        "edges": edge_count,
        "tiers": {tier: len(tiers[tier]) for tier in TIERS},
        "mustRatio": round(must_ratio, 3),
        "branches": len(used_tiers),
        "unresolved": [{"node": n, "prerequisite": p} for n, p in unresolved],
        "unknown": unknown_nodes,
        "noEvidence": no_evidence,
        "cycles": cycles,
        "duplicates": duplicates,
        "violations": violations,
    }
    return "\n".join(lines) + "\n", summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", required=True, help="学习库根目录")
    parser.add_argument("--out", help="输出文件，默认 <kb>/views/结构地图.md")
    parser.add_argument("--strict", action="store_true", help="门槛未过时退出 1")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    kb = Path(args.kb)
    if not kb.is_dir():
        print(f"学习库不存在：{kb}", file=sys.stderr)
        return 2
    map_path = kb / "01-知识地图.md"
    if not map_path.is_file():
        print(f"缺 01-知识地图.md：{map_path}", file=sys.stderr)
        return 2

    text, summary = build(kb)
    if summary["nodes"] == 0:
        print("01-知识地图.md 没有可解析的知识点行", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else kb / "views/结构地图.md"
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    summary["out"] = out.as_posix()
    summary["dryRun"] = args.dry_run
    summary["bytes"] = len(text.encode("utf-8"))
    summary["ok"] = not summary["cycles"] and not summary["violations"]

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"nodes={summary['nodes']} edges={summary['edges']} "
            f"must={summary['tiers']['必掌握']} violations={len(summary['violations'])} -> {out}"
        )
    if summary["cycles"]:
        return 1
    if args.strict and summary["violations"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
