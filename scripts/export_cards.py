#!/usr/bin/env python3
"""Export records/cards.jsonl to one Obsidian note per card.

Why one file per card: the Obsidian Spaced Repetition plugin keys scheduling
state by note path (and card position). The `views/` directory is derived and
regenerated, so cards placed there would lose their review history on every
rebuild. A stable `notes/cards/CARD-0001-<slug>.md` path keeps the schedule.

Cards use the multi-line syntax:

    看到……该怎么做？
    ?
    答案正文
    #review

Usage:
  python export_cards.py --kb 学习库
  python export_cards.py --kb 学习库 --all --json
  python export_cards.py --kb 学习库 --out 学习库/notes/cards --dry-run

Exit codes: 0 = exported; 2 = bad args or missing knowledge base.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

BAD_NAME = re.compile(r'[\\/:*?"<>|#^\[\]\s]+')
CARD_FILE = re.compile(r"^(CARD-\d+)-")

TYPE_PROMPTS = {
    "定义": "「{title}」的定义与记号是什么？",
    "定理": "「{title}」的条件与结论是什么？什么时候不能用？",
    "步骤": "看到「{signal}」这类信号，按什么步骤处理？",
    "陷阱": "「{title}」在什么信号下容易错？怎么避开？",
}


def load_cards(path: Path) -> tuple[list[dict], int]:
    cards: list[dict] = []
    skipped = 0
    if not path.is_file():
        return cards, skipped
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(item, dict) and item.get("id"):
            cards.append(item)
        else:
            skipped += 1
    return cards, skipped


def yaml_scalar(value: object) -> str:
    text = "" if value is None else str(value)
    if text == "":
        return '""'
    if (
        re.search(r'[:#\[\]{}&*!|>\'"%@`]', text)
        or text.strip() != text
        or re.match(r"^[\-?:,\[\]{}&*!|>'\"%@`]", text)  # 前导符号会被当成列表/键
        or re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
        or text.lower() in {"true", "false", "yes", "no", "on", "off", "null", "~"}
    ):
        return json.dumps(text, ensure_ascii=False)
    return text


def yaml_list(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "[]"
    return json.dumps([str(v) for v in values], ensure_ascii=False)


def slugify(title: str) -> str:
    slug = BAD_NAME.sub("-", title).strip("-")
    return slug[:40].strip("-")


def signal_text(card: dict) -> str:
    signal = card.get("signal")
    if isinstance(signal, list):
        return "；".join(str(s) for s in signal if str(s).strip())
    return str(signal or "").strip()


def prompt_for(card: dict) -> str:
    title = str(card.get("title") or card.get("id") or "")
    signal = signal_text(card)
    return TYPE_PROMPTS.get(str(card.get("type") or ""), "「{title}」该怎么做？").format(
        title=title, signal=signal or title
    )


def build_note(card: dict, generated: str) -> str:
    cid = str(card.get("id"))
    ctype = str(card.get("type") or "")
    title = str(card.get("title") or cid)
    signal = signal_text(card)
    prompt = prompt_for(card)

    body: list[str] = []
    if card.get("method"):
        body.append(f"**方法**：{card['method']}")
    steps = card.get("steps")
    if isinstance(steps, list) and steps:
        body.append("**步骤**：")
        body.extend(f"{i}. {step}" for i, step in enumerate(steps, 1))
    if card.get("boundary"):
        body.append(f"**边界**：{card['boundary']}")
    evidence = card.get("evidence")
    if isinstance(evidence, list) and evidence:
        body.append("**证据**：" + "；".join(str(e) for e in evidence))
    errors = card.get("errors")
    if isinstance(errors, list) and errors:
        body.append("**相关错题**：" + "；".join(str(e) for e in errors))

    lines = [
        "---",
        f"id: {cid}",
        f"type: {yaml_scalar(ctype)}",
        f"title: {yaml_scalar(title)}",
        f"status: {yaml_scalar(card.get('status'))}",
        f"evidence: {yaml_list(evidence)}",
        f"errors: {yaml_list(errors)}",
        f"parent: {yaml_scalar(card.get('parent'))}",
        "tags:",
        "  - study-coach/card",
        "  - review",
        f"source: records/cards.jsonl",
        f"generated: {generated}",
        "---",
        "",
        f"# {cid} · {title}",
        "",
    ]
    if signal:
        lines += [f"**触发信号**：{signal}", ""]
    lines += [prompt, "?", ""]
    lines += body or ["（卡片内容待补）"]
    lines += ["", "#review", ""]
    return "\n".join(lines)


def existing_path(out: Path, cid: str) -> Path | None:
    matches = sorted(p for p in out.glob(f"{cid}-*.md") if CARD_FILE.match(p.name))
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", required=True, help="学习库根目录")
    parser.add_argument("--out", help="卡片目录，默认 <kb>/notes/cards")
    parser.add_argument("--all", action="store_true", help="含 retired 卡片")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    kb = Path(args.kb)
    if not kb.is_dir():
        print(f"学习库不存在：{kb}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else kb / "notes/cards"
    cards, skipped_lines = load_cards(kb / "records/cards.jsonl")
    generated = date.today().isoformat()

    created: list[str] = []
    updated: list[str] = []
    unchanged = 0
    retired_skipped = 0
    touched: set[Path] = set()

    for card in cards:
        cid = str(card.get("id"))
        if str(card.get("status", "")).lower() == "retired" and not args.all:
            retired_skipped += 1
            continue
        target = existing_path(out_dir, cid) if out_dir.is_dir() else None
        if target is None:
            slug = slugify(str(card.get("title") or cid)) or "card"
            target = out_dir / f"{cid}-{slug}.md"
        touched.add(target)
        content = build_note(card, generated)
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            unchanged += 1
            continue
        existed = target.is_file()
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (updated if existed else created).append(target.as_posix())

    known = {str(c.get("id")) for c in cards}
    orphans: list[str] = []
    if out_dir.is_dir():
        for path in sorted(out_dir.glob("CARD-*.md")):
            match = CARD_FILE.match(path.name)
            if match and match.group(1) not in known:
                orphans.append(path.as_posix())

    summary = {
        "kb": kb.as_posix(),
        "out": out_dir.as_posix(),
        "cards": len(cards),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "retiredSkipped": retired_skipped,
        "orphans": orphans,
        "skippedLines": skipped_lines,
        "dryRun": args.dry_run,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"cards={len(cards)} created={len(created)} updated={len(updated)} "
            f"unchanged={unchanged} retired={retired_skipped} "
            f"orphans={len(orphans)} skipped={skipped_lines}"
        )
        for path in orphans:
            print(f"孤儿卡片（源记录已不存在）：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
