#!/usr/bin/env python3
"""Export records/cards.jsonl as an Anki-importable TSV file.

Uses Anki's text-file headers (2.1.54+):

  #separator:tab / #html:true / #notetype column / #deck column
  #tags column / #guid column

GUID is the card ID, so re-importing updates the same notes instead of
duplicating them. No API key and no AnkiConnect required.

Usage:
  python export_anki.py --kb 学习库
  python export_anki.py --kb 学习库 --deck "考研::数学" --all --json

Then in Anki: File > Import, pick views/anki-cards.tsv.

Exit codes: 0 = exported; 2 = missing knowledge base or bad args.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

from export_cards import prompt_for, signal_text

HEADERS = [
    "#separator:tab",
    "#html:true",
    "#notetype column:5",
    "#deck column:4",
    "#tags column:3",
    "#guid column:6",
]
TAG_SPACE = re.compile(r"\s+")


def one_line(value: object) -> str:
    return TAG_SPACE.sub(" ", str(value or "")).strip()


def tag_part(value: str) -> str:
    return TAG_SPACE.sub("_", value.strip()) or "未分类"


def htmlize(value: object) -> str:
    text = html.escape(str(value or ""), quote=False).replace("\t", " ")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def back_html(card: dict) -> str:
    lines: list[str] = []
    if card.get("method"):
        lines.append(f"<b>方法</b>：{htmlize(card['method'])}")
    steps = card.get("steps")
    if isinstance(steps, list) and steps:
        lines.append("<b>步骤</b>：")
        lines.extend(f"{i}. {htmlize(step)}" for i, step in enumerate(steps, 1))
    if card.get("boundary"):
        lines.append(f"<b>边界</b>：{htmlize(card['boundary'])}")
    evidence = card.get("evidence")
    if isinstance(evidence, list) and evidence:
        lines.append("<b>证据</b>：" + htmlize("；".join(str(e) for e in evidence)))
    return "<br>".join(lines) or "（卡片内容待补）"


def row_for(card: dict, deck: str, notetype: str) -> tuple[list[str], bool]:
    cid = str(card.get("id"))
    signal = signal_text(card)
    prompt = prompt_for(card)
    front = htmlize(prompt)
    if signal and signal not in prompt:
        front += f"<br><br><i>信号：{htmlize(signal)}</i>"
    ctype = one_line(card.get("type")) or "未分类"
    tags = " ".join(dict.fromkeys(["study-coach", f"study-coach::{tag_part(ctype)}", "review"]))
    back = back_html(card)
    incomplete = back == "（卡片内容待补）"
    fields = [
        front,
        back,
        tags,
        one_line(deck) or "study-coach",
        one_line(notetype) or "Basic",
        cid,
    ]
    if any("\t" in field or "\n" in field for field in fields):
        raise ValueError(f"卡片 {cid} 的字段含制表符或换行，会破坏 TSV")
    return fields, incomplete


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", required=True, help="学习库根目录")
    parser.add_argument("--out", help="输出文件，默认 <kb>/views/anki-cards.tsv")
    parser.add_argument("--deck", default="study-coach", help="Anki 牌组名，默认 study-coach")
    parser.add_argument("--notetype", default="Basic", help="Anki 笔记类型，默认 Basic")
    parser.add_argument("--all", action="store_true", help="含 retired 卡片")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    kb = Path(args.kb)
    if not kb.is_dir():
        print(f"学习库不存在：{kb}", file=sys.stderr)
        return 2
    if "\t" in args.deck or "\n" in args.deck or "\t" in args.notetype or "\n" in args.notetype:
        print("--deck / --notetype 不能含制表符或换行", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else kb / "views/anki-cards.tsv"
    cards, skipped = load_cards(kb / "records/cards.jsonl")
    rows: list[str] = list(HEADERS)
    exported = 0
    retired_skipped = 0
    incomplete: list[str] = []
    try:
        for card in cards:
            if str(card.get("status", "")).lower() == "retired" and not args.all:
                retired_skipped += 1
                continue
            fields, thin = row_for(card, args.deck, args.notetype)
            if thin:
                incomplete.append(str(card.get("id")))
            rows.append("\t".join(fields))
            exported += 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    text = "\n".join(rows) + "\n"
    if not args.dry_run:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")

    summary = {
        "kb": kb.as_posix(),
        "out": out.as_posix(),
        "cards": len(cards),
        "exported": exported,
        "retiredSkipped": retired_skipped,
        "incomplete": incomplete,
        "skippedLines": skipped,
        "bytes": len(text.encode("utf-8")),
        "dryRun": args.dry_run,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(
            f"exported={exported} retired={retired_skipped} "
            f"incomplete={len(incomplete)} skipped={skipped} -> {out}"
        )
        if incomplete:
            print("内容待补：" + "、".join(incomplete))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
