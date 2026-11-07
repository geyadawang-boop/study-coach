#!/usr/bin/env python3
"""Build and query a local Markdown index with SQLite FTS5 + char-bigram vector.

No third-party dependency. Markdown files stay the source of truth; the
database is a rebuildable cache. Chinese text uses FTS5 trigram tokenization,
so BM25 queries need at least 3 characters; vector mode accepts shorter ones.
Search defaults to hybrid RRF (BM25 + char-bigram cosine).

Usage:
  python kb_index.py build <kb_dir> [--db index.db]
  python kb_index.py search <db> "query" [--top 5] [--kind 例题] [--mode hybrid]
  python kb_index.py eval <db> <queries.jsonl> [--top 5] [--mode hybrid]
  python kb_index.py stats <db>

Exit codes: 0 = 正常有结果；1 = 无命中（search 输出 no match）或
eval 未达到全中（属评估信号，不是崩溃）；2 = 库/索引/查询文件不可用。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

CHUNK_TARGET = 600
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS docs(
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            title TEXT,
            hash TEXT NOT NULL,
            mtime REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY,
            doc_id INTEGER NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
            ord INTEGER NOT NULL,
            heading TEXT,
            kind TEXT,
            text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text, heading, title, tokenize='trigram'
        );
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    if "kind" not in columns:
        conn.execute("ALTER TABLE chunks ADD COLUMN kind TEXT")
    return conn


CHUNK_KINDS = ("定理", "步骤", "例题", "陷阱", "其他")


CHUNK_RULES = (
    ("例题", r"例题|典例|真题|例\s*\d|例[：:]"),
    ("陷阱", r"陷阱|易错|注意|误区|坑|误解|错因|错题"),
    ("定理", r"定理|定义|性质|引理|推论|公式"),
    ("步骤", r"步骤|方法|模板|流程|算法|套路"),
)


def chunk_kind(heading: str, doc_title: str = "") -> str:
    """Classify a chunk by its most specific heading, then by document title.

    Only the last heading segment decides, so an H1 like 「公式渲染约定」 does
    not turn every child chunk into 定理. A misconception or example
    collection is homogeneous, so its document title is used as fallback.
    """
    parts = [part.strip() for part in (heading or "").split(">") if part.strip()]
    local = parts[-1] if parts else ""
    for kind, pattern in CHUNK_RULES:
        if re.search(pattern, local):
            return kind
    for kind, pattern in (
        ("陷阱", r"误解|错题|易错"),
        ("例题", r"例题集|真题解析|习题集"),
        ("定理", r"术语|公式速查|定义表|公式表"),
    ):
        if re.search(pattern, doc_title or ""):
            return kind
    if len(parts) <= 1:
        for kind, pattern in CHUNK_RULES:
            if re.search(pattern, heading or ""):
                return kind
    return "其他"


def normalize_math(text: str) -> str:
    """Collapse the most common equivalent LaTeX spellings before indexing."""
    text = re.sub(r"\$+", " ", text)
    text = text.replace("\\left", "").replace("\\right", "")
    text = re.sub(r"\\[dt]frac", r"\\frac", text)
    text = re.sub(r"\\[,;:!]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _tokens(text: str) -> list[str]:
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", text.lower())
    return [compact[i:i + 2] for i in range(max(0, len(compact) - 1))]


def _cosine(query: dict[str, int], text: str) -> float:
    if not query or not text:
        return 0.0
    counts: dict[str, int] = {}
    for token in _tokens(text[:4000]):
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return 0.0
    dot = sum(value * counts.get(token, 0) for token, value in query.items())
    if not dot:
        return 0.0
    import math

    qnorm = math.sqrt(sum(value * value for value in query.values()))
    tnorm = math.sqrt(sum(value * value for value in counts.values()))
    return dot / (qnorm * tnorm)


def split_markdown(text: str) -> list[tuple[str, str]]:
    """Return (heading_path, chunk_text) pairs, splitting on headings and size."""
    chunks: list[tuple[str, str]] = []
    stack: list[str] = []
    buf: list[str] = []
    buf_heading = ""

    def flush() -> None:
        nonlocal buf, buf_heading
        body = "\n".join(buf).strip()
        if body:
            chunks.append((buf_heading, body))
        buf = []

    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            del stack[max(0, level - 1):]
            stack.append(title)
            buf_heading = " > ".join(stack)
            continue
        buf.append(line)
        if sum(len(x) + 1 for x in buf) >= CHUNK_TARGET:
            flush()
            buf_heading = " > ".join(stack)
    flush()

    # A document with only headings still needs its title indexed.
    if not chunks and text.strip():
        chunks.append(("", text.strip()[:CHUNK_TARGET]))
    return chunks


def build(kb_dir: Path, db: Path) -> None:
    if not kb_dir.is_dir():
        raise SystemExit(f"not a directory: {kb_dir}")
    conn = connect(db)
    files = []
    for path in sorted(kb_dir.rglob("*.md")):
        if not path.is_file():
            continue
        parts = path.relative_to(kb_dir).parts
        if parts[0] == "views" or any(part.startswith(".") for part in parts):
            continue  # views/ 是派生层，索引它会重复命中；隐藏目录同理
        if len(parts) == 1 and path.name == "02-资料清单.md":
            continue  # 由 sources.jsonl 渲染的视图，同属派生层
        files.append(path)
    seen: set[str] = set()
    added = updated = skipped = 0

    for path in files:
        rel = path.relative_to(kb_dir).as_posix()
        seen.add(rel)
        raw = path.read_text(encoding="utf-8", errors="replace")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        row = conn.execute("SELECT id, hash FROM docs WHERE path = ?", (rel,)).fetchone()
        if row and row[1] == digest:
            skipped += 1
            continue
        title = path.stem
        first = HEADING.match(raw.lstrip().splitlines()[0]) if raw.strip() else None
        if first and len(first.group(1)) == 1:
            title = first.group(2).strip()

        if row:
            doc_id = row[0]
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM chunks_fts WHERE rowid NOT IN (SELECT id FROM chunks)")
            conn.execute(
                "UPDATE docs SET title = ?, hash = ?, mtime = ? WHERE id = ?",
                (title, digest, path.stat().st_mtime, doc_id),
            )
            updated += 1
        else:
            cur = conn.execute(
                "INSERT INTO docs(path, title, hash, mtime) VALUES (?, ?, ?, ?)",
                (rel, title, digest, path.stat().st_mtime),
            )
            doc_id = cur.lastrowid
            added += 1

        for ord_no, (heading, body) in enumerate(split_markdown(raw)):
            heading_norm = normalize_math(heading)
            body_norm = normalize_math(body)
            cur = conn.execute(
                "INSERT INTO chunks(doc_id, ord, heading, kind, text) VALUES (?, ?, ?, ?, ?)",
                (doc_id, ord_no, heading_norm, chunk_kind(heading, title), body_norm),
            )
            conn.execute(
                "INSERT INTO chunks_fts(rowid, text, heading, title) VALUES (?, ?, ?, ?)",
                (cur.lastrowid, body_norm, heading_norm, title),
            )

    stale = [r[0] for r in conn.execute("SELECT path FROM docs").fetchall() if r[0] not in seen]
    for rel in stale:
        row = conn.execute("SELECT id FROM docs WHERE path = ?", (rel,)).fetchone()
        if row:
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (row[0],))
        conn.execute("DELETE FROM docs WHERE path = ?", (rel,))
    conn.execute("DELETE FROM chunks_fts WHERE rowid NOT IN (SELECT id FROM chunks)")
    conn.commit()

    total_docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    print(
        f"indexed={len(files)} added={added} updated={updated} skipped={skipped} "
        f"removed={len(stale)} docs={total_docs} chunks={total_chunks} db={db}"
    )


def match_expression(query: str) -> str:
    """Turn a query into an FTS5 expression.

    The trigram tokenizer can only match 3+ character terms, and Chinese
    queries usually arrive as one unspaced sentence. So each whitespace
    separated keyword becomes an OR of its sliding trigrams; keywords are
    ANDed with each other. BM25 then ranks chunks by how many windows match.
    """
    groups = []
    for piece in normalize_math(query).split():
        piece = piece.replace('"', "")
        if len(piece) < 3:
            continue
        grams: list[str] = []
        seen: set[str] = set()
        for start in range(len(piece) - 2):
            gram = piece[start:start + 3]
            if gram not in seen:
                seen.add(gram)
                grams.append(gram)
            if len(grams) >= 64:
                break
        groups.append("(" + " OR ".join(f'"{g}"' for g in grams) + ")")
    return " AND ".join(groups) if groups else '"' + normalize_math(query) + '"'


def bm25_rows(conn, query: str, top: int, kind: str | None = None):
    sql = """
        SELECT c.id, d.path, c.heading, c.kind,
               snippet(chunks_fts, 0, '[', ']', '…', 16) AS snip,
               bm25(chunks_fts, 1.0, 3.0, 2.0) AS score
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN docs d ON d.id = c.doc_id
        WHERE chunks_fts MATCH ?
    """
    params: list = [match_expression(query)]
    if kind:
        sql += " AND c.kind = ?"
        params.append(kind)
    sql += " ORDER BY score LIMIT ?"
    params.append(top)
    return conn.execute(sql, params).fetchall()


def vector_rows(conn, query: str, top: int, kind: str | None = None):
    weights: dict[str, int] = {}
    for token in _tokens(query):
        weights[token] = weights.get(token, 0) + 1
    sql = "SELECT c.id, d.path, c.heading, c.kind, c.text FROM chunks c JOIN docs d ON d.id = c.doc_id"
    params: list = []
    if kind:
        sql += " WHERE c.kind = ?"
        params.append(kind)
    rows = []
    for chunk_id, path, heading, chunk_type, text in conn.execute(sql, params):
        score = _cosine(weights, f"{heading or ''} {text}")
        if score > 0:
            rows.append((chunk_id, path, heading, chunk_type, text[:200], score))
    rows.sort(key=lambda item: -item[5])
    return rows[:top]


def search_rows(conn, query: str, top: int, kind: str | None = None, mode: str = "hybrid"):
    if mode == "bm25":
        return bm25_rows(conn, query, top, kind)
    if mode == "vector":
        return vector_rows(conn, query, top, kind)

    pool = max(top * 4, 20)
    fused: dict[int, list] = {}
    for rank, row in enumerate(bm25_rows(conn, query, pool, kind), 1):
        fused[row[0]] = [row, 1.0 / (60 + rank)]
    for rank, row in enumerate(vector_rows(conn, query, pool, kind), 1):
        entry = fused.setdefault(row[0], [row, 0.0])
        entry[1] += 1.0 / (60 + rank)
    ranked = sorted(fused.values(), key=lambda item: -item[1])[:top]
    return [
        (row[0], row[1], row[2], row[3], row[4], round(score, 4))
        for row, score in ranked
    ]


def open_index(db: Path) -> sqlite3.Connection:
    """Open an existing index; never create one implicitly on read paths."""
    if not db.is_file():
        print(
            f"index not found: {db}  (run: kb_index.py build <kb_dir> --db {db})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        conn = connect(db)
        conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
    except sqlite3.DatabaseError as exc:
        print(
            f"cannot read index {db}: {exc}  (delete {db.parent} and rebuild)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return conn


def search(db: Path, query: str, top: int, kind: str | None = None, mode: str = "hybrid") -> int:
    if mode == "bm25" and len(query.strip()) < 3:
        print("query must be at least 3 characters for trigram search", file=sys.stderr)
        return 2
    conn = open_index(db)
    try:
        rows = search_rows(conn, query, top, kind, mode)
    finally:
        conn.close()

    if not rows:
        print("no match")
        return 1
    for _chunk_id, path, heading, chunk_type, snip, score in rows:
        where = f"{path} › {heading}" if heading else path
        print(f"- [{chunk_type or '其他'}] {where}  ({mode}={score:.4f})\n  {snip}")
    return 0


def evaluate(
    db: Path,
    queries_path: Path,
    top: int,
    kind: str | None = None,
    mode: str = "hybrid",
) -> int:
    """Measure hit@k / MRR against a JSONL query set.

    Each line: {"query": "...", "expect": ["LEC-002", "第2章"]}
    A hit means any expected substring appears in a result's path or heading.
    """
    queries = []
    if not queries_path.is_file():
        print(f"query set not found: {queries_path}", file=sys.stderr)
        return 2
    with queries_path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                query = str(item["query"])
                expect = [str(x) for x in item.get("expect", [])]
            except (json.JSONDecodeError, KeyError) as exc:
                print(f"bad query line {line_no}: {exc}", file=sys.stderr)
                return 2
            queries.append((query, expect))

    if not queries:
        print("empty query set", file=sys.stderr)
        return 2

    conn = open_index(db)
    hits = 0
    reciprocal = 0.0
    try:
        for query, expect in queries:
            rows = search_rows(conn, query, top, kind, mode)
            rank = None
            for index, row in enumerate(rows, 1):
                where = f"{row[1]} › {row[2] or ''}".lower()
                if any(token.lower() in where for token in expect):
                    rank = index
                    break
            if rank:
                hits += 1
                reciprocal += 1.0 / rank
            print(f"query={query} hit@{top}={'yes' if rank else 'no'} rank={rank or '-'}")
    finally:
        conn.close()

    print(
        f"summary mode={mode} queries={len(queries)} hit@{top}={hits}/{len(queries)} "
        f"mrr={reciprocal / len(queries):.3f}"
    )
    return 0 if hits == len(queries) else 1


def stats(db: Path) -> None:
    conn = open_index(db)
    try:
        docs = conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
        chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        size = db.stat().st_size if db.exists() else 0
        print(f"docs={docs} chunks={chunks} db_bytes={size}")
        for chunk_type, count in conn.execute(
            "SELECT COALESCE(kind, '其他'), COUNT(*) FROM chunks GROUP BY kind ORDER BY COUNT(*) DESC"
        ):
            print(f"  kind {chunk_type}: {count}")
        for path, count in conn.execute(
            "SELECT d.path, COUNT(*) AS n FROM chunks c JOIN docs d ON d.id = c.doc_id "
            "GROUP BY d.id ORDER BY n DESC LIMIT 10"
        ):
            print(f"  {count:>4}  {path}")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser("build", help="index a Markdown directory")
    p_build.add_argument("kb_dir")
    p_build.add_argument("--db", default="index.db")

    p_search = sub.add_parser("search", help="BM25 + vector hybrid search")
    p_search.add_argument("db")
    p_search.add_argument("query")
    p_search.add_argument("--top", type=int, default=5)
    p_search.add_argument("--kind", choices=CHUNK_KINDS, help="只看某一类切块")
    p_search.add_argument("--mode", choices=("hybrid", "bm25", "vector"), default="hybrid")

    p_stats = sub.add_parser("stats", help="show index size")
    p_stats.add_argument("db")

    p_eval = sub.add_parser("eval", help="hit@k / MRR on a JSONL query set")
    p_eval.add_argument("db")
    p_eval.add_argument("queries")
    p_eval.add_argument("--top", type=int, default=5)
    p_eval.add_argument("--kind", choices=CHUNK_KINDS)
    p_eval.add_argument("--mode", choices=("hybrid", "bm25", "vector"), default="hybrid")

    args = parser.parse_args()
    if args.cmd == "build":
        build(Path(args.kb_dir), Path(args.db))
        return 0
    if args.cmd == "search":
        return search(Path(args.db), args.query, max(1, args.top), args.kind, args.mode)
    if args.cmd == "eval":
        return evaluate(
            Path(args.db), Path(args.queries), max(1, args.top), args.kind, args.mode
        )
    stats(Path(args.db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
