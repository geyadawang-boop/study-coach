#!/usr/bin/env python3
"""FSRS scheduling on top of records/reviews.jsonl.

Replays every review for a card in time order, so the scheduler state is
derived from the same records the rest of the knowledge base already uses.
`--grade CARD:1-4` asks what the next interval/due would be; `--record` also
appends the review (with an auto REV id) so the answer survives the session.

The four-level ladder stays as an explicit fallback (`--engine ladder`) and as
the automatic fallback when `fsrs` is not installed; both engines read and
write the same fields (`rating/interval_days/due`), so records remain portable.

Usage:
  python review_schedule.py --kb 学习库
  python review_schedule.py --kb 学习库 --card CARD-0007 --json
  python review_schedule.py --kb 学习库 --grade CARD-0007:3 --at 2026-11-08
  python review_schedule.py --kb 学习库 --grade CARD-0007:3 --record

Exit codes: 0 = schedule built; 2 = bad input/arguments; 3 = --engine fsrs
requested but the fsrs package is missing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
LADDER = {1: 1, 2: 3, 3: 7, 4: 16}
LADDER_LONG = 35
REV_ID = re.compile(r"^REV-(\d{4,})$")
GRADE = re.compile(r"^([^:]+):([1-4])$")


def parse_at(value: str | None) -> datetime:
    if not value:
        return datetime.combine(date.today(), time(9, 0), tzinfo=TZ)
    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), time(9, 0), tzinfo=TZ)
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"--at 不是合法日期：{value}") from None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def review_time(value) -> datetime | None:
    try:
        if len(str(value)) == 10:
            return datetime.combine(date.fromisoformat(str(value)), time(9, 0), tzinfo=TZ)
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def load(path: Path) -> tuple[list[dict], int]:
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


def card_ids(kb: Path) -> set[str]:
    rows, _ = load(kb / "records/cards.jsonl")
    return {str(row.get("id") or "") for row in rows}


def next_rev_id(rows: list[dict]) -> int:
    highest = 0
    for row in rows:
        match = REV_ID.match(str(row.get("id") or ""))
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def numeric_id(card_id: str) -> int:
    digits = "".join(ch for ch in card_id if ch.isdigit())
    if digits:
        return int(digits)
    return sum(ord(ch) for ch in card_id)


def ladder_interval(ratings: list[int]) -> int:
    if len(ratings) >= 2 and ratings[-1] >= 3 and ratings[-2] >= 3:
        return LADDER_LONG
    return LADDER.get(ratings[-1], 1) if ratings else 1


def fsrs_scheduler():
    try:
        from fsrs import Scheduler
    except ImportError:
        return None
    return Scheduler()


def replay_fsrs(history: list[dict], scheduler, warnings: list[str]) -> dict:
    from fsrs import Card, Rating

    card = Card(card_id=None)
    used = 0
    for row in sorted(history, key=lambda r: (str(r.get("ts") or ""), str(r.get("id") or ""))):
        try:
            rating = int(row.get("rating"))
        except (TypeError, ValueError):
            warnings.append(f"{row.get('id')} rating 不是整数，已跳过")
            continue
        if rating not in LADDER:
            warnings.append(f"{row.get('id')} rating={rating} 不在 1–4，已跳过")
            continue
        when = review_time(row.get("ts"))
        if when is None:
            warnings.append(f"{row.get('id')} ts 无法解析，已跳过")
            continue
        card.card_id = numeric_id(str(row.get("card_id") or "")) or None
        card, _ = scheduler.review_card(
            card, Rating(rating), review_datetime=when.astimezone(timezone.utc)
        )
        used += 1
    return {"card": card, "used": used}


def describe(card, at: datetime) -> dict:
    last = card.last_review
    due_local = card.due.astimezone(TZ) if card.due else None
    interval = 0.0
    if last and card.due:
        interval = (card.due - last).total_seconds() / 86400
    overdue = 0
    if due_local:
        overdue = max(0, (at.date() - due_local.date()).days)
    return {
        "state": getattr(getattr(card, "state", None), "name", str(getattr(card, "state", ""))).lower(),
        "step": getattr(card, "step", None),
        "stability": round(card.stability, 4) if getattr(card, "stability", None) else None,
        "difficulty": round(card.difficulty, 4) if getattr(card, "difficulty", None) else None,
        "last_review": last.astimezone(TZ).isoformat() if last else None,
        "due": due_local.isoformat() if due_local else None,
        "due_date": due_local.date().isoformat() if due_local else None,
        "interval_days": round(interval, 2),
        "overdue_days": overdue,
    }


def append_records(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.is_file() and path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                prefix = "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prefix)
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", required=True, help="学习库根目录")
    parser.add_argument("--card", action="append", help="只看该卡（可重复）")
    parser.add_argument("--grade", action="append", default=[], help="模拟评分 CARD:1-4（可重复）")
    parser.add_argument("--at", help="评分/到期基准日，默认今天（YYYY-MM-DD 或 ISO 时间）")
    parser.add_argument("--engine", choices=["auto", "fsrs", "ladder"], default="auto")
    parser.add_argument("--record", action="store_true", help="把 --grade 结果追加到 reviews.jsonl")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    kb = Path(args.kb)
    if not kb.is_dir():
        print(f"学习库不存在：{kb}", file=sys.stderr)
        return 2
    try:
        at = parse_at(args.at)
        grades = []
        for spec in args.grade:
            match = GRADE.match(spec.strip())
            if not match:
                print(f"--grade 格式应为 CARD:1-4，收到 {spec}", file=sys.stderr)
                return 2
            grades.append((match.group(1).strip(), int(match.group(2))))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    known = card_ids(kb)
    if args.record:
        if not grades:
            print("--record 需要同时给 --grade", file=sys.stderr)
            return 2
        unknown = sorted({cid for cid, _ in grades if cid not in known})
        if unknown:
            print(f"--record 拒绝写入：cards.jsonl 中没有 {', '.join(unknown)}", file=sys.stderr)
            return 2

    reviews, skipped = load(kb / "records/reviews.jsonl")
    warnings: list[str] = []
    if skipped:
        warnings.append(f"reviews.jsonl 跳过 {skipped} 行无法解析的 JSON")

    by_card: dict[str, list[dict]] = {}
    for row in reviews:
        card_id = str(row.get("card_id") or "").strip()
        if not card_id:
            warnings.append(f"{row.get('id')} 缺 card_id，已跳过")
            continue
        by_card.setdefault(card_id, []).append(row)

    scheduler = fsrs_scheduler() if args.engine in {"auto", "fsrs"} else None
    if args.engine == "fsrs" and scheduler is None:
        print("--engine fsrs 需要先安装：pip install fsrs", file=sys.stderr)
        return 3
    engine = "fsrs" if scheduler else "ladder"
    if args.engine == "auto" and scheduler is None:
        warnings.append("未安装 fsrs，自动降级为四档阶梯（1/3/7/16，连对 35 天）")

    selected = list(args.card or sorted(by_card))
    if not args.card:
        for card_id in sorted(known):  # 零复习的新卡也要出现在排期里
            if card_id not in selected:
                selected.append(card_id)
    for card_id, _ in grades:
        if card_id not in selected:
            selected.append(card_id)

    cards_out: list[dict] = []
    states: dict[str, dict] = {}
    for card_id in selected:
        history = sorted(by_card.get(card_id, []), key=lambda r: (
            str(r.get("ts") or ""), str(r.get("id") or "")))  # 协议要求按 ts 顺序重建状态
        if card_id not in known:
            warnings.append(f"{card_id} 不在 cards.jsonl 中，按新卡排期")
        if engine == "fsrs":
            built = replay_fsrs(history, scheduler, warnings)
            card = built["card"]
            info = describe(card, at)
            info.update({"card_id": card_id, "reviews": built["used"]})
            states[card_id] = {"card": card}
        else:
            ratings = [int(r.get("rating")) for r in history
                       if str(r.get("rating", "")).isdigit() and int(r.get("rating")) in LADDER]
            interval = ladder_interval(ratings) if ratings else 0
            last = str(history[-1].get("ts") or "") if history else ""
            due = (review_time(last) or at) + timedelta(days=interval) if ratings else None
            info = {
                "card_id": card_id, "reviews": len(ratings), "state": "ladder", "step": None,
                "stability": None, "difficulty": None,
                "last_review": last or None,
                "due": due.isoformat() if due else None,
                "due_date": due.date().isoformat() if due else None,
                "interval_days": interval,
                "overdue_days": max(0, (at.date() - due.date()).days) if due else 0,
            }
            states[card_id] = {"ratings": ratings, "due": due}
        cards_out.append(info)

    grades_out: list[dict] = []
    records: list[dict] = []
    next_id = next_rev_id(reviews)
    for card_id, rating in grades:
        state = states.get(card_id)
        if engine == "fsrs":
            from fsrs import Rating
            if state is None:
                built = replay_fsrs(by_card.get(card_id, []), scheduler, warnings)
                state = {"card": built["card"]}
                states[card_id] = state
            card, _ = scheduler.review_card(
                state["card"], Rating(rating), review_datetime=at.astimezone(timezone.utc)
            )
            state["card"] = card
            info = describe(card, at)
        else:
            if state is None:
                state = {"ratings": [], "due": None}
                states[card_id] = state
            ratings = state["ratings"] + [rating]
            state["ratings"] = ratings
            interval = ladder_interval(ratings)
            due = at + timedelta(days=interval)
            state["due"] = due
            info = {
                "state": "ladder", "step": None, "stability": None, "difficulty": None,
                "last_review": at.isoformat(), "due": due.isoformat(),
                "due_date": due.date().isoformat(), "interval_days": interval, "overdue_days": 0,
            }
        info.update({"card_id": card_id, "rating": rating})
        grades_out.append(info)
        if args.record:
            records.append({
                "schema": 1,
                "id": f"REV-{next_id:04d}",
                "card_id": card_id,
                "ts": at.date().isoformat(),
                "rating": rating,
                "interval_days": int(round(info["interval_days"] or 0)),
                "due": info["due_date"],
                "engine": engine,
            })
            next_id += 1

    if args.record and records:
        append_records(kb / "records/reviews.jsonl", records)

    payload = {
        "engine": engine,
        "at": at.isoformat(),
        "cards": cards_out,
        "grades": grades_out,
        "recorded": records,
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"engine={engine} at={at.date().isoformat()} cards={len(cards_out)}")
        for row in cards_out:
            print(f"{row['card_id']} reviews={row['reviews']} state={row['state']} "
                  f"due={row['due_date'] or '-'} interval_days={row['interval_days']} "
                  f"overdue_days={row['overdue_days']}")
        for row in grades_out:
            print(f"grade {row['card_id']}:{row['rating']} -> due={row['due_date']} "
                  f"interval_days={row['interval_days']} state={row['state']}")
        if records:
            print(f"wrote {len(records)} record(s) to {kb / 'records/reviews.jsonl'}")
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
