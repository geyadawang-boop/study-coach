#!/usr/bin/env python3
"""Check a 学习库 for structural and referential problems.

Read-only: this script never rewrites records. It reports findings at three
levels — error (data is wrong), warn (data is likely wrong or stale), info
(normal but worth knowing).

Usage:
  python kb_health.py <kb_dir> [--today 2026-11-07] [--strict] [--json]

Exit code: 2 if any error, 1 if --strict and any warning, else 0.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ID_SPEC = {
    "cards": ("CARD", 4),
    "errors": ("ERR", 4),
    "confusions": ("CONF", 4),
    "reviews": ("REV", 4),
    "events": ("EVT", 6),
    "sources": ("DOC", 4),
    "corrections": ("CORR", 4),
}
LAYERS = {"scope", "authority", "exam"}
KINDS = {"textbook", "supplement", "syllabus", "past-paper", "notes", "reference"}
TRUST = {"high", "medium", "low"}
CARD_STATUS = {"hypothesis", "verified", "retired"}
CONSEQUENCES = {"丢分", "结论错", "表述不规范"}
RATINGS = {1, 2, 3, 4}


class Report:
    def __init__(self) -> None:
        self.findings: list[dict] = []

    def add(self, level: str, code: str, msg: str) -> None:
        self.findings.append({"level": level, "code": code, "msg": msg})

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f["level"] == level)


def first_token(value) -> str:
    text = str(value or "").strip()
    return text.split()[0] if text else ""


def load_jsonl(path: Path) -> tuple[list[dict], list[tuple[int, str]]]:
    rows: list[dict] = []
    problems: list[tuple[int, str]] = []
    if not path.exists():
        return rows, problems
    with path.open(encoding="utf-8-sig") as handle:
        for no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append((no, str(exc)))
                continue
            if not isinstance(item, dict):
                problems.append((no, "不是 JSON 对象"))
                continue
            rows.append(item)
    return rows, problems


def _tail(rows: list, limit: int) -> tuple[list, str]:
    if len(rows) <= limit:
        return rows, ""
    return rows[:limit], f"（另有 {len(rows) - limit} 条同类，略）"


def check(kb: Path, today: str | None = None) -> Report:
    rep = Report()
    today = today or date.today().isoformat()

    if not kb.is_dir():
        rep.add("error", "kb_missing", f"学习库不存在：{kb}")
        return rep

    records = kb / "records"
    notes_dir = kb / "notes"
    note_stems = {p.stem for p in notes_dir.glob("*.md")} if notes_dir.is_dir() else set()

    def note_exists(stem: str) -> bool:
        # Notes are named LEC-003-主题.md, so an ID-only reference is valid.
        return stem in note_stems or any(s.startswith(stem + "-") for s in note_stems)

    data: dict[str, list[dict]] = {}
    for name, (prefix, width) in ID_SPEC.items():
        path = records / f"{name}.jsonl"
        rows, problems = load_jsonl(path)
        for no, why in problems:
            rep.add("error", "jsonl_parse", f"{path.name} 第 {no} 行无法解析：{why}")
        data[name] = rows

        seen: dict[str, int] = {}
        pattern = re.compile(rf"^{prefix}-\d{{{width}}}$")
        for idx, row in enumerate(rows, 1):
            rid = str(row.get("id") or "").strip()
            if not rid:
                rep.add("error", "id_missing", f"{path.name} 第 {idx} 条记录缺 id")
                continue
            if not pattern.match(rid):
                rep.add("error", "id_format",
                        f"{path.name} 第 {idx} 条 id={rid}，应为 {prefix}-{width} 位数字")
            seen[rid] = seen.get(rid, 0) + 1
        for rid, count in seen.items():
            if count > 1:
                rep.add("error", "id_dup",
                        f"{path.name} 中 {rid} 出现 {count} 次；更正应新增记录 + supersedes，不能重号")

    cards = data["cards"]
    errors = data["errors"]
    reviews = data["reviews"]
    sources = data["sources"]
    corrections = data["corrections"]
    card_ids = {str(c.get("id") or "") for c in cards}
    error_ids = {str(e.get("id") or "") for e in errors}

    for card in cards:
        cid = card.get("id")
        parent = str(card.get("parent") or "").strip()
        if parent and parent not in card_ids:
            rep.add("error", "ref_card_parent", f"{cid} 的 parent={parent} 不在 cards.jsonl")
        for ref in card.get("errors") or []:
            if str(ref) not in error_ids:
                rep.add("error", "ref_card_error", f"{cid} 关联的 {ref} 不在 errors.jsonl")
        for ev in card.get("evidence") or []:
            stem = first_token(ev)
            if stem.startswith(("TP-", "LEC-")) and not note_exists(stem):
                rep.add("warn", "ref_note_missing",
                        f"{cid} 的代表题/讲义 {stem} 没有对应的 notes/{stem}.md")
        status = str(card.get("status", "hypothesis")).lower()
        if status not in CARD_STATUS:
            rep.add("warn", "card_status",
                    f"{cid} 状态 {status} 不在 hypothesis/verified/retired 内")
        if not str(card.get("boundary") or "").strip():
            rep.add("warn", "card_boundary", f"{cid} 缺 boundary；没有反例条件说明还没想清楚")

    for err in errors:
        eid = err.get("id")
        src = first_token(err.get("source"))
        if src.startswith(("TP-", "LEC-")) and not note_exists(src):
            rep.add("warn", "ref_note_missing",
                    f"{eid} 的来源 {src} 没有对应的 notes/{src}.md")
        consequence = err.get("consequence")
        if consequence not in (None, "") and str(consequence) not in CONSEQUENCES:
            rep.add("warn", "error_consequence",
                    f"{eid} consequence={consequence}，只允许 丢分/结论错/表述不规范")

    for correction in corrections:
        cid = correction.get("id")
        ref = str(correction.get("ref") or "").strip()
        if ref and ref not in card_ids and not note_exists(ref):
            rep.add("error", "ref_correction", f"{cid} 的 ref={ref} 既不是卡片也不是 notes/ 下的笔记")
        status = str(correction.get("status") or "").lower()
        if status not in {"open", "fixed"}:
            rep.add("warn", "correction_status", f"{cid} status={status or '空'}，只允许 open/fixed")
        if not str(correction.get("ai_pattern") or "").strip():
            rep.add("warn", "correction_pattern", f"{cid} 缺 ai_pattern，纠错无法回流到讲解自检")

    for review in reviews:
        rid = review.get("id")
        card_id = str(review.get("card_id") or "")
        if card_id not in card_ids:
            rep.add("error", "ref_review_card", f"{rid} 指向不存在的卡片 {card_id or '(空)'}")
        try:
            rating = int(review.get("rating"))
        except (TypeError, ValueError):
            rep.add("error", "review_rating", f"{rid} rating 不是整数：{review.get('rating')}")
            continue
        if rating not in RATINGS:
            rep.add("error", "review_rating", f"{rid} rating={rating} 不在 1–4")

    latest: dict[str, tuple[tuple[str, str], dict]] = {}
    for review in reviews:
        card_id = str(review.get("card_id") or "")
        if not card_id:
            continue
        key = (str(review.get("ts") or ""), str(review.get("id") or ""))
        if card_id not in latest or key >= latest[card_id][0]:
            latest[card_id] = (key, review)

    overdue = sorted(
        (str(r.get("due") or ""), cid)
        for cid, (_, r) in latest.items()
        if str(r.get("due") or "") and str(r.get("due")) < today
    )
    shown, more = _tail(overdue, 10)
    for due, cid in shown:
        rep.add("warn", "review_overdue", f"{cid} 到期 {due} 未复习")
    if more:
        rep.add("warn", "review_overdue", f"还有 {len(overdue) - 10} 张卡到期未复习{more}")

    doc_ids = {str(s.get("id") or "") for s in sources}
    for source in sources:
        sid = str(source.get("id") or "") or "未命名资料"
        layer = str(source.get("layer") or "")
        kind = str(source.get("kind") or "")
        trust = str(source.get("trust") or "")
        coverage = source.get("coverage")
        missing = []
        if layer not in LAYERS:
            missing.append(f"layer={layer or '空'}")
        if kind not in KINDS:
            missing.append(f"kind={kind or '空'}")
        if trust not in TRUST:
            missing.append(f"trust={trust or '空'}")
        if not isinstance(coverage, list) or not [c for c in coverage if str(c).strip()]:
            missing.append("coverage 为空")
        if missing:
            rep.add("error", "source_tags", f"{sid} 四标不全：{'；'.join(missing)}")
        supersedes = source.get("supersedes")
        if supersedes and str(supersedes) not in doc_ids:
            rep.add("error", "source_supersedes",
                    f"{sid} 的 supersedes={supersedes} 不存在")

    progress = kb / "progress.md"
    if progress.is_file():
        text = progress.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"最近事件[：:]\s*(EVT-\d{6})", text)
        event_ids = {str(e.get("id") or "") for e in data["events"]}
        if match and match.group(1) not in event_ids:
            rep.add("warn", "progress_pointer",
                    f"progress.md 指向 {match.group(1)}，但 events.jsonl 中没有该事件")
        if not match:
            rep.add("info", "progress_format", "progress.md 缺「最近事件：EVT-xxxxxx」指针")

    views = kb / "views"
    if views.is_dir():
        newest_record = max((p.stat().st_mtime for p in records.glob("*.jsonl")), default=0.0)
        for view in views.glob("*.md"):
            head = view.read_text(encoding="utf-8", errors="replace")[:200]
            if "generated by scripts/" not in head:
                rep.add("warn", "view_handedit", f"{view.name} 缺生成标记，可能被手改过")
            elif view.stat().st_mtime < newest_record - 1:
                rep.add("info", "view_stale", f"{view.name} 比 records/ 旧，建议重跑 kb_view.py")

    try:
        horizon = (datetime.fromisoformat(today) - timedelta(days=30)).date().isoformat()
    except ValueError:
        horizon = ""
    if horizon:
        reviewed = {str(r.get("card_id") or "") for r in reviews}
        aging = [
            str(c.get("id"))
            for c in cards
            if str(c.get("status", "hypothesis")).lower() == "hypothesis"
            and str(c.get("id")) not in reviewed
            and str(c.get("ts") or "") < horizon
        ]
        shown, more = _tail(aging, 10)
        for cid in shown:
            rep.add("info", "card_aging", f"{cid} 超过 30 天既未验证也未复习")
        if more:
            rep.add("info", "card_aging", f"另有 {len(aging) - 10} 张卡同样滞留{more}")

    if not any(data.values()):
        rep.add("info", "kb_empty", "学习库为空（首次使用正常），没有可检查的记录")

    return rep


def render(rep: Report) -> str:
    lines: list[str] = []
    for level, label in (("error", "错误"), ("warn", "警告"), ("info", "提示")):
        items = [f for f in rep.findings if f["level"] == level]
        if not items:
            continue
        lines.append(f"## {label}（{len(items)}）")
        lines.extend(f"- [{f['code']}] {f['msg']}" for f in items)
        lines.append("")
    lines.append(
        f"errors={rep.count('error')} warnings={rep.count('warn')} infos={rep.count('info')}"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kb_dir")
    parser.add_argument("--today", help="覆盖当天日期（YYYY-MM-DD），便于复现")
    parser.add_argument("--strict", action="store_true", help="有警告也返回非零")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    rep = check(Path(args.kb_dir), args.today)
    if args.json:
        payload = {
            "errors": rep.count("error"),
            "warnings": rep.count("warn"),
            "infos": rep.count("info"),
            "findings": rep.findings,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(rep))

    if rep.count("error"):
        return 2
    if args.strict and rep.count("warn"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
