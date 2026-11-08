#!/usr/bin/env python3
"""Build a short session-opening digest from records, without reading notes.

Context cannot be reclaimed, only condensed. This script reads the pointer
file plus the JSONL records (never `notes/`), and prints a bounded summary so
a new session can resume without loading whole chapters into the window.

Usage:
  python session_digest.py --kb 学习库
  python session_digest.py --kb 学习库 --max-chars 1200 --days 7 --json

Exit codes: 0 = digest built; 2 = missing knowledge base or bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

TRUNCATE_NOTE = "\n…（已截断，完整内容在 records/ 与 progress.md）"


def load_jsonl(path: Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    skipped = 0
    if not path.is_file():
        return rows, skipped
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(item, dict):
            rows.append(item)
        else:
            skipped += 1
    return rows, skipped


def row_date(row: dict) -> date | None:
    raw = str(row.get("ts") or row.get("date") or "")
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def short(value: object, limit: int = 60) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build(kb: Path, days: int, at: date) -> tuple[str, dict]:
    start = at - timedelta(days=days - 1)
    read_files: list[str] = []
    skipped = 0

    def records(name: str) -> list[dict]:
        path = kb / "records" / name
        rows, bad = load_jsonl(path)
        nonlocal skipped
        skipped += bad
        if path.is_file():
            read_files.append(path.relative_to(kb).as_posix())
        return rows

    progress_path = kb / "progress.md"
    progress = ""
    if progress_path.is_file():
        progress = progress_path.read_text(encoding="utf-8-sig").strip()
        read_files.append("progress.md")

    events = records("events.jsonl")
    reviews = records("reviews.jsonl")
    errors = records("errors.jsonl")
    cards = records("cards.jsonl")
    confusions = records("confusions.jsonl")

    lines = ["# 会话开场摘要", ""]

    lines += ["## 进度指针", ""]
    if progress:
        clipped = progress if len(progress) <= 700 else progress[:700] + "…"
        lines += [clipped, ""]
    else:
        lines += ["（无 progress.md：按新会话处理，先做三问再继续）", ""]

    recent_events = [e for e in events if (d := row_date(e)) and start <= d <= at]
    recent_events.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    lines += [f"## 最近事件（{days} 天内，最多 5 条）", ""]
    if recent_events:
        for event in recent_events[:5]:
            lines.append(
                f"- {str(event.get('ts') or '')[:10]} {event.get('id', '?')} "
                f"{event.get('action') or event.get('stage') or ''} "
                f"{event.get('result') or ''} {short(event.get('ref'), 20)}".rstrip()
            )
    else:
        lines.append("- （窗口内无事件）")
    lines.append("")

    window_reviews = [r for r in reviews if (d := row_date(r)) and start <= d <= at]
    latest: dict[str, dict] = {}
    for review in reviews:
        cid = str(review.get("card_id") or "")
        when = str(review.get("ts") or "")
        if cid and when >= str(latest.get(cid, {}).get("ts") or ""):
            latest[cid] = review
    due = []
    for cid, review in latest.items():
        due_raw = str(review.get("due") or "")[:10]
        try:
            due_date = date.fromisoformat(due_raw)
        except ValueError:
            continue
        if due_date <= at:
            due.append((due_date, cid))
    due.sort()
    lines += ["## 复习", ""]
    if latest:
        overdue = f"，最早 {due[0][0].isoformat()}（{due[0][1]}）" if due else ""
        lines.append(
            f"- {days} 天内复习 {len(window_reviews)} 次；到期 {len(due)} 张{overdue}"
        )
    else:
        lines.append("- （暂无复习记录：先建卡并跑 review_schedule.py）")
    lines.append("")

    open_errors = [
        e for e in errors if str(e.get("status", "open")).lower() not in {"closed", "fixed", "retired"}
    ]
    open_errors.sort(key=lambda e: str(e.get("ts") or ""), reverse=True)
    lines += ["## 未闭环错题", ""]
    if open_errors:
        for err in open_errors[:3]:
            lines.append(
                f"- {err.get('id', '?')} {short(err.get('concept') or err.get('desc'), 40)}"
                f"（{short(err.get('desc') if err.get('concept') else err.get('fix'), 50)}）"
            )
        if len(open_errors) > 3:
            lines.append(f"- 另有 {len(open_errors) - 3} 条，用 kb_stats.py 汇总")
    else:
        lines.append("- （无未闭环错题）")
    lines.append("")

    pending = [
        c for c in cards if str(c.get("status", "hypothesis")).lower() == "hypothesis"
    ]
    lines += ["## 待验证卡", ""]
    if pending:
        ids = "、".join(str(c.get("id")) for c in pending[:5])
        lines.append(f"- {len(pending)} 张 hypothesis：{ids}")
    else:
        lines.append("- （无待验证卡）")
    lines.append("")

    freq: dict[str, int] = {}
    for item in confusions:
        if (d := row_date(item)) and not (start <= d <= at):
            continue
        tag = str(item.get("tag") or item.get("note") or "").strip()
        if tag:
            try:
                count = int(item.get("ask_count") or 1)
            except (TypeError, ValueError):
                count = 1
            freq[tag] = freq.get(tag, 0) + count
    lines += [f"## 高频困惑（{days} 天内）", ""]
    if freq:
        for tag, count in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:3]:
            lines.append(f"- {count} 次：{short(tag, 50)}")
    else:
        lines.append("- （窗口内无困惑记录）")
    lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    meta = {
        "kb": kb.as_posix(),
        "days": days,
        "at": at.isoformat(),
        "chars": len(text),
        "skippedLines": skipped,
        "readFiles": read_files,
    }
    return text, meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", required=True, help="学习库根目录")
    parser.add_argument("--max-chars", type=int, default=2000, help="输出字符上限（默认 2000）")
    parser.add_argument("--days", type=int, default=7, help="回看天数（默认 7）")
    parser.add_argument("--at", help="基准日 YYYY-MM-DD，默认今天")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    kb = Path(args.kb)
    if not kb.is_dir():
        print(f"学习库不存在：{kb}", file=sys.stderr)
        return 2
    if args.max_chars < 200:
        print("--max-chars 至少 200，否则摘要没有信息量", file=sys.stderr)
        return 2
    if args.days < 1:
        print("--days 必须 ≥ 1", file=sys.stderr)
        return 2
    try:
        at = date.fromisoformat(args.at) if args.at else date.today()
    except ValueError:
        print(f"--at 不是合法日期：{args.at}", file=sys.stderr)
        return 2

    text, meta = build(kb, args.days, at)
    truncated = len(text) > args.max_chars
    if truncated:
        keep = max(0, args.max_chars - len(TRUNCATE_NOTE) - 1)
        text = text[:keep].rstrip() + TRUNCATE_NOTE + "\n"
    meta["chars"] = len(text)
    meta["truncated"] = truncated

    if args.json:
        print(json.dumps({**meta, "digest": text}, ensure_ascii=False, indent=2))
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
