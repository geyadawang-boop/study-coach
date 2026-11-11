#!/usr/bin/env python3
"""零 API Key 网页检索：Brave HTML → Yahoo HTML，带预算、域名过滤与质量门。

只发 GET，解析结果页 HTML；不登录、不带 key、不落盘。每个查询只发一次请求，
主查询无结果或只剩放宽结果时才会补发一次 --fallback-query。
离线自测用 --fixture-brave / --fixture-yahoo 注入本地 HTML。

用法:
  python scripts/web_search.py "tanx-sinx/x^3 一题多解" \
      --include-domains bilibili.com,math.stackexchange.com
  python scripts/web_search.py "tanx-sinx/x^3 一题多解" \
      --fallback-query "limit (tan x - sin x)/x^3"   # 首个查询没结果才用
  python scripts/web_search.py "..." --engine brave \
      --fixture-brave evals/fixtures/search-brave.html

退出码:
  0  结果通过质量门
  1  degraded：有结果但弱（少于 min-results、title/snippet 全空，或域名白名单全滤空后放宽）
  2  无结果、被反爬拦截、超时或参数错误

输出: JSON，字段与 references/solution-research.md 的检索契约一致。
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BRAVE_URL = "https://search.brave.com/search?q={}"
YAHOO_URL = "https://search.yahoo.com/search?p={}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")
ACCEPT = ("text/html,application/xhtml+xml,application/xml;q=0.9,"
          "image/avif,image/webp,*/*;q=0.8")
LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"
CURL = shutil.which("curl")
CHALLENGE = re.compile(
    r"(captcha|unusual\s+traffic|verify\s+you\s+are\s+a?\s*human|"
    r"challenge-platform|cf-chl-|access\s+denied)", re.I)
PROVIDERS = {"brave": "brave-html", "yahoo": "yahoo-html"}


def now_ms() -> int:
    return int(time.monotonic() * 1000)


def clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return " ".join(html_lib.unescape(text).split())


def clip(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def absolute(url: str) -> str:
    return urllib.parse.urljoin("https://", url.strip())


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def domain_match(host: str, domain: str) -> bool:
    domain = domain.strip().lower().lstrip(".")
    return bool(domain) and (host == domain or host.endswith("." + domain))


def detect_challenge(page: str) -> str:
    hit = CHALLENGE.search(page)
    return f"challenge-detected:{hit.group(1).lower()}" if hit else ""


def parse_brave(page: str) -> list[dict]:
    """按标题锚点解析；摘要在标题锚点之后、下一个结果标题之前，取第一个正文块。"""
    results: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>", page, re.S | re.I):
        href = re.search(r"href\s*=\s*[\"']([^\"']+)[\"']", m.group("attrs"), re.I)
        if not href or "search-snippet-title" not in m.group("body"):
            continue
        url = absolute(href.group(1))
        if url.lower() in seen:
            continue
        title = ""
        tag = re.search(
            r"<(?P<tag>[a-z0-9]+)\b[^>]*class\s*=\s*[\"'][^\"']*"
            r"search-snippet-title[^\"']*[\"'][^>]*>",
            m.group("body"), re.I)
        if tag:
            title_attr = re.search(r"title\s*=\s*[\"']([^\"']+)[\"']", tag.group(0), re.I)
            if title_attr:
                title = clean(title_attr.group(1))
            if not title:
                rest = m.group("body")[tag.end():]
                close = re.search(rf"</{tag.group('tag')}\s*>", rest, re.I)
                title = clean(rest[: close.start()] if close else rest[:200])
        # 摘要不在标题锚点里，取锚点之后到下一个结果标题之间的片段
        window = page[m.end():]
        cut = window.find("search-snippet-title")
        if cut != -1:
            window = window[:cut]
        snippet = ""
        desc = re.search(r"<div\b[^>]*class\s*=\s*[\"']content\b[^\"']*[\"'][^>]*>(.*?)</div>",
                         window, re.S | re.I)
        if desc:
            snippet = clip(clean(desc.group(1)))
        if not snippet:
            generic = re.search(r"class\s*=\s*[\"'][^\"']*generic-snippet[^\"']*[\"'][^>]*>(.*?)</div>",
                                window, re.S | re.I)
            if generic:
                snippet = clip(clean(generic.group(1)))
        if not snippet:
            # 版式变动兜底：该结果区间去标签后至少给出可判读的正文
            snippet = clip(clean(window))
        if not title:
            title = clip(clean(snippet))[:200]
        if title:
            seen.add(url.lower())
            results.append({"title": clip(title), "url": url, "snippet": snippet})
    return results


def parse_yahoo(page: str) -> list[dict]:
    results: list[dict] = []
    blocks = re.findall(r"<li\b[^>]*>(.*?)</li>", page, re.S | re.I)
    if not any("data-matarget" in block for block in blocks):
        blocks = [page]
    for block in blocks:
        anchor = re.search(
            r"<a\b(?P<attrs>[^>]*data-matarget\s*=\s*[\"']algo[\"'][^>]*)>"
            r"(?P<body>.*?)</a>", block, re.S | re.I)
        if not anchor:
            continue
        href = re.search(r"href\s*=\s*[\"']([^\"']+)[\"']", anchor.group("attrs"), re.I)
        if not href:
            continue
        url = href.group(1)
        redirect = re.search(r"/RU=(.+?)(?:/RK=|RK=)", url)
        if redirect:
            url = urllib.parse.unquote(redirect.group(1))
        body = anchor.group("body")
        h3 = re.search(r"<h3\b[^>]*>(.*?)</h3>", body, re.S | re.I)
        title = clean(h3.group(1)) if h3 else clean(body)[:200]
        snippet = ""
        comp = re.search(r"class\s*=\s*[\"'][^\"']*compText[^\"']*[\"'][^>]*>"
                         r"(.*?)</div>", block, re.S | re.I)
        if comp:
            snippet = clean(comp.group(1))[:300]
        if title:
            results.append({"title": clip(title), "url": absolute(url), "snippet": snippet})
    return results


def fetch_curl(url: str, timeout: float) -> tuple[str, str]:
    """curl.exe 传输：Yahoo 对 urllib 的 TLS/HTTP2 指纹常回 500，curl 稳定。"""
    command = [CURL, "-sS", "-L", "--compressed", "--max-time", f"{max(1.0, timeout):.1f}",
               "-A", UA, "-H", f"Accept: {ACCEPT}", "-H", f"Accept-Language: {LANGUAGE}",
               "-w", "\n%{http_code}", url]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=timeout + 3)
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except OSError as exc:
        return "", f"request-error:{type(exc).__name__}"
    body, sep, code = proc.stdout.rpartition(b"\n")
    status = code.strip().decode("ascii", errors="ignore")
    if not sep or not status:
        return "", "request-error:no-status"
    if status in ("403", "429"):
        return "", "upstream-http-blocked"
    if not status.startswith("2"):
        return "", f"upstream-http-{status}"
    return body.decode("utf-8", errors="replace"), ""


def fetch(url: str, timeout: float) -> tuple[str, str]:
    """优先 curl.exe；没有 curl 时退回 urllib（5xx 重试一次）。"""
    if CURL:
        return fetch_curl(url, timeout)
    return fetch_urllib(url, timeout)


def fetch_urllib(url: str, timeout: float) -> tuple[str, str]:
    """最多 2 次 GET：5xx 与连接错误重试一次，403/429/验证码不重试。"""
    request = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": ACCEPT,
        "Accept-Language": LANGUAGE,
    })
    deadline = time.monotonic() + timeout
    reason = "timeout"
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0.5:
            break
        try:
            with urllib.request.urlopen(request, timeout=remaining) as resp:
                raw = resp.read(5_000_000)
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace"), ""
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                return "", "upstream-http-blocked"
            reason = f"upstream-http-{exc.code}"
            if exc.code < 500:
                return "", reason
        except socket.timeout:
            return "", "timeout"
        except urllib.error.URLError as exc:
            reason = f"upstream-request-failed:{exc.reason}"
        except Exception as exc:  # 网络之外的解码/协议错误也留原始原因
            return "", f"request-error:{type(exc).__name__}"
        if attempt == 0:
            time.sleep(min(0.8, max(0.0, deadline - time.monotonic() - 0.5)))
    return "", reason


def select_bounded(results: list[dict], max_chars: int) -> tuple[list[dict], int]:
    kept: list[dict] = []
    used = 0
    for item in results:
        cost = len(item["title"]) + len(item["url"]) + len(item["snippet"])
        if cost > max_chars or used + cost > max_chars:
            continue
        kept.append(item)
        used += cost
    return kept, len(results) - len(kept)


def rank_preferred(results: list[dict], preferred: list[str]) -> tuple[list[dict], int]:
    if not preferred:
        return results, 0

    def rank(item: dict) -> tuple[int, int]:
        host = host_of(item["url"])
        for index, domain in enumerate(preferred):
            if domain_match(host, domain):
                return index, 0
        return len(preferred), 1

    ordered = sorted(enumerate(results), key=lambda pair: (rank(pair[1]), pair[0]))
    marked = [item for item in results if rank(item)[0] < len(preferred)]
    return [item for _, item in ordered], len(marked)


def search_once(query: str, args: argparse.Namespace, include: list[str],
                preferred: list[str], deadline: int) -> tuple[list[dict], str, list[dict], bool]:
    """对一个查询跑一遍引擎回退链，返回（结果、provider、尝试记录、是否放宽了域名白名单）。"""
    providers = ["brave", "yahoo"] if args.engine == "auto" else [args.engine]
    attempts: list[dict] = []
    best: list[dict] = []
    best_provider = "none"
    best_relaxed = False

    for name in providers:
        remaining = (deadline - now_ms()) / 1000
        if remaining <= 0:
            attempts.append({"provider": PROVIDERS[name], "status": "error", "reason": "timeout",
                             "resultCount": 0})
            break
        fixture = getattr(args, f"fixture_{name}")
        if fixture:
            try:
                page = Path(fixture).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                attempts.append({"provider": PROVIDERS[name], "status": "error",
                                 "reason": f"fixture-unreadable:{exc}", "resultCount": 0})
                continue
            reason = ""
        else:
            url = (BRAVE_URL if name == "brave" else YAHOO_URL).format(
                urllib.parse.quote_plus(query))
            page, reason = fetch(url, remaining)
            if reason:
                attempts.append({"provider": PROVIDERS[name], "status": "error",
                                 "reason": reason, "resultCount": 0})
                continue

        raw = parse_brave(page) if name == "brave" else parse_yahoo(page)
        if not raw:
            # 只在解析不到结果时查反爬：正常结果页正文出现 captcha 字样不算被拦
            blocked = detect_challenge(page)
            if blocked:
                attempts.append({"provider": PROVIDERS[name], "status": "blocked",
                                 "reason": blocked, "resultCount": 0})
                continue

        unique: list[dict] = []
        seen: set[str] = set()
        for item in raw:
            key = item["url"].lower()
            if key in seen or not item["url"].startswith(("http://", "https://")):
                continue
            seen.add(key)
            unique.append(item)

        filtered = [item for item in unique
                    if not include or any(domain_match(host_of(item["url"]), d) for d in include)]
        relaxed = False
        if not filtered and unique:
            # 白名单把结果全滤掉：保留原结果并降为 degraded，交给下游标注「未按域名过滤」
            filtered, relaxed = unique, True
        if not filtered:
            attempts.append({"provider": PROVIDERS[name], "status": "empty",
                             "reason": "no-results", "resultCount": 0})
            continue

        ranked, _ = rank_preferred(filtered, preferred)
        kept, omitted = select_bounded(ranked[: args.max_results], args.max_chars)
        if not kept:
            attempts.append({"provider": PROVIDERS[name], "status": "empty",
                             "reason": "budget-exhausted", "resultCount": 0})
            continue

        weak = (relaxed or len(kept) < args.min_results
                or not any(item["title"] or item["snippet"] for item in kept))
        attempts.append({
            "provider": PROVIDERS[name], "status": "degraded" if weak else "ok",
            "reason": "filter-relaxed" if relaxed else ("weak-results" if weak else ""),
            "resultCount": len(kept), "budgetOmittedResults": omitted,
            "filterRelaxed": relaxed,
        })
        if not best or (best_relaxed and not relaxed) or (
                relaxed == best_relaxed and len(kept) > len(best)):
            best, best_provider, best_relaxed = kept, PROVIDERS[name], relaxed
        if not weak:
            break
    return best, best_provider, attempts, best_relaxed


def run(args: argparse.Namespace) -> tuple[dict, int]:
    started = now_ms()
    deadline = started + int(args.timeout * 1000)
    include = [d.strip() for d in (args.include_domains or "").split(",") if d.strip()]
    preferred = [d.strip() for d in (args.preferred_domains or "").split(",") if d.strip()]
    queries = [args.query]
    fallback = (args.fallback_query or "").strip()
    if fallback and fallback != args.query:
        queries.append(fallback)

    attempts: list[dict] = []
    best: list[dict] = []
    best_provider = "none"
    filter_relaxed = False
    used_query = queries[0]
    for query in queries:
        got, provider, tries, relaxed = search_once(
            query, args, include, preferred, deadline)
        attempts.extend(tries)
        # 放宽过白名单的结果先兜底；备选查询若拿到站内结果，再覆盖它
        if got and (not best or not relaxed):
            best, best_provider, used_query, filter_relaxed = got, provider, query, relaxed
        if best and not filter_relaxed:
            break

    elapsed = now_ms() - started
    result_chars = sum(len(i["title"]) + len(i["url"]) + len(i["snippet"]) for i in best)
    ok = bool(best) and attempts and attempts[-1]["status"] == "ok"
    first_failure = next((a["reason"] for a in attempts if a["status"] != "ok"), "")
    payload = {
        "status": "ok" if ok else ("degraded" if best else "error"),
        "source": "web-search",
        "query": used_query,
        "queriesTried": queries,
        "fallbackUsed": used_query != queries[0],
        "filterRelaxed": filter_relaxed,
        "route": args.engine,
        "provider": best_provider if best else "none",
        "providerAttempts": attempts,
        "firstFailureReason": first_failure,
        "results": best,
        "elapsedMs": elapsed,
        "apiKeyUsed": False,
        "outputBudgetChars": args.max_chars,
        "resultChars": result_chars,
        "budgetOmittedResults": sum(a.get("budgetOmittedResults", 0) for a in attempts),
        "qualityGate": {
            "enabled": True,
            "weak": not ok,
            "reason": "" if ok else (first_failure or "no-results"),
        },
    }
    if ok:
        return payload, 0
    return payload, (1 if best else 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="检索词；用题目核心式，不塞整段题面")
    parser.add_argument("--engine", choices=["auto", "brave", "yahoo"], default="auto")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--min-results", type=int, default=1, help="低于该数判 weak")
    parser.add_argument("--timeout", type=float, default=30.0, help="整条链路总预算（秒）")
    parser.add_argument("--max-chars", type=int, default=2800, help="title+url+snippet 字符预算")
    parser.add_argument("--include-domains", default="", help="只保留这些域名，逗号分隔；全滤空时降级保留并标 filterRelaxed")
    parser.add_argument("--preferred-domains", default="", help="这些域名排前面，逗号分隔")
    parser.add_argument("--fallback-query", default="", help="首个查询无结果时再试的备选查询（如英文关键词）")
    parser.add_argument("--fixture-brave", help="离线 Brave 结果页 HTML")
    parser.add_argument("--fixture-yahoo", help="离线 Yahoo 结果页 HTML")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if args.max_results < 1 or args.min_results < 1 or args.timeout <= 0 or args.max_chars < 1:
        print(json.dumps({"status": "error", "reason": "invalid-argument"}, ensure_ascii=False))
        return 2

    payload, code = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
