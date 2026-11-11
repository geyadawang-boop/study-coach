#!/usr/bin/env python3
"""Run the acceptance checks declared in checks.json.

Checks are implemented in this file; checks.json is the manifest that fixes
their order, level and description. Keep the two in sync — a mismatch is
reported as an error.

Usage:
  python run_checks.py [--skill-root .] [--kb 学习库] [--json]

Exit code: 0 all checks passed (skipped checks do not fail), 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # 导入 kb_health 时不要在 skill 树里留 __pycache__
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"  # 子进程同样不留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb_health  # noqa: E402

REQUIRED = [
    "SKILL.md",
    "checks.json",
    "agents/openai.yaml",
    "evals/cases.jsonl",
    "references/source-layers.md",
    "references/lecture-and-drill.md",
    "references/paper-intake.md",
    "references/error-taxonomy.md",
    "references/knowledge-base.md",
    "references/output-contract.md",
    "references/tech-stack.md",
    "references/research.md",
    "references/capabilities.json",
    "evals/retrieval.jsonl",
    "evals/routing.jsonl",
    "scripts/route.py",
    "evals/fixtures/paper-good.md",
    "evals/fixtures/paper-bad.md",
    "evals/fixtures/questions-good.jsonl",
    "evals/fixtures/questions-bad.jsonl",
    "scripts/kb_index.py",
    "scripts/kb_view.py",
    "scripts/kb_stats.py",
    "scripts/kb_health.py",
    "scripts/step_check.py",
    "scripts/explain_lint.py",
    "evals/fixtures/explain-solution-good.md",
    "evals/fixtures/explain-followup-good.md",
    "evals/fixtures/explain-multi-good.md",
    "scripts/export_book.py",
    "scripts/intake_check.py",
    "scripts/web_search.py",
    "scripts/export_cards.py",
    "scripts/session_digest.py",
    "scripts/export_anki.py",
    "scripts/structure_map.py",
    "scripts/cheatsheet.py",
    "scripts/fitz_compat.py",
    "evals/fixtures/search-brave.html",
    "evals/fixtures/search-yahoo.html",
    "evals/fixtures/search-challenge.html",
    "scripts/run_checks.py",
    "subjects/calculus/syllabus.md",
    "subjects/calculus/render.md",
    "subjects/calculus/exam-patterns.md",
    "subjects/calculus/misconceptions.md",
    "subjects/calculus/glossary.md",
]
ROUTE = re.compile(r"(?:references/[\w\-./]+\.md|scripts/[\w\-./]+\.py)")
EVAL_TYPES = {"trigger", "non_trigger", "behavior"}


def finding(level: str, msg: str) -> dict:
    return {"level": level, "msg": msg}


def check_layout(root: Path) -> list[dict]:
    out = [finding("error", f"缺文件：{rel}") for rel in REQUIRED if not (root / rel).is_file()]
    if not (root / "subjects").is_dir():
        out.append(finding("error", "缺 subjects/ 目录"))
    return out


def check_frontmatter(root: Path) -> list[dict]:
    out: list[dict] = []
    text = (root / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return [finding("error", "SKILL.md 未以 frontmatter 开头")]
    end = text.find("\n---", 3)
    if end < 0:
        return [finding("error", "SKILL.md frontmatter 未闭合")]
    block = text[3:end]

    name = re.search(r"^name:\s*(\S+)\s*$", block, re.MULTILINE)
    desc = re.search(r"^description:\s*(.+?)(?=^\w|\Z)", block, re.MULTILINE | re.DOTALL)
    if not name:
        out.append(finding("error", "frontmatter 缺 name"))
    elif name.group(1) != root.name:
        out.append(finding("error", f"name={name.group(1)} 与目录名 {root.name} 不一致"))
    if not desc:
        out.append(finding("error", "frontmatter 缺 description"))
    else:
        body = " ".join(desc.group(1).split())
        if len(body) < 60:
            out.append(finding("error", f"description 过短（{len(body)} 字符），无法路由"))
        if "Use when" not in body:
            out.append(finding("warn", "description 缺英文触发语「Use when ...」"))
    return out


def check_routing(root: Path) -> list[dict]:
    out: list[dict] = []
    text = (root / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    mentioned = set(ROUTE.findall(text))
    for rel in sorted(mentioned):
        if not (root / rel).is_file():
            out.append(finding("error", f"SKILL.md 提到 {rel}，但文件不存在"))
    for pattern in ("references/*.md", "scripts/*.py"):
        for path in sorted(root.glob(pattern)):
            rel = path.relative_to(root).as_posix()
            if rel not in mentioned:
                out.append(finding("warn", f"{rel} 未在 SKILL.md 资源路由中出现"))
    return out


def check_routing_eval(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/route.py"
    path = root / "evals/routing.jsonl"
    seen: set[str] = set()
    for no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            out.append(finding("error", f"routing.jsonl 第 {no} 行无法解析：{exc}"))
            continue
        cid = str(case.get("id") or "")
        if not cid:
            out.append(finding("error", f"routing.jsonl 第 {no} 行缺 id"))
        elif cid in seen:
            out.append(finding("error", f"routing.jsonl 第 {no} 行 id={cid} 重复"))
        seen.add(cid)
        expect = str(case.get("expect") or "")
        if not str(case.get("input") or "").strip() or not expect:
            out.append(finding("error", f"{cid or no} 缺 input/expect"))
        elif not (root / expect).is_file():
            out.append(finding("error", f"{cid} 的 expect={expect} 不存在"))
    if len(seen) < 20:
        out.append(finding("warn", f"路由评测只有 {len(seen)} 条，覆盖偏少"))

    proc = subprocess.run(
        [sys.executable, str(script), "--eval", str(path), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        out.append(finding("error", f"route.py --eval 跑不起来：{proc.stderr.strip()}"))
        return out
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        out.append(finding("error", f"route.py --eval 输出不是 JSON：{proc.stdout[:120]}"))
        return out
    rate1 = float(data.get("rate1") or 0)
    rate3 = float(data.get("rate3") or 0)
    if rate1 < 0.85:
        out.append(finding("error", f"路由 top1={rate1:.0%}，低于 85%"))
    if rate3 < 0.95:
        out.append(finding("error", f"路由 top3={rate3:.0%}，低于 95%"))
    if len(data.get("rows") or []) != len(seen):
        out.append(finding("error", "评测结果行数与 routing.jsonl 用例数不一致"))

    direct = subprocess.run(
        [sys.executable, str(script), "两栏试卷跨页怎么切", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if direct.returncode != 0:
        out.append(finding("error", f"route.py 单句路由失败：{direct.stderr.strip()}"))
    else:
        ranked = json.loads(direct.stdout).get("ranked") or []
        if len(ranked) != 3 or ranked[0]["entry"] != "scripts/page_regions.py":
            out.append(finding("error", f"单句路由 top3 异常：{ranked}"))

    sys.path.insert(0, str(root / "scripts"))
    import route  # noqa: E402

    covered = {item["entry"] for item in route.INTENTS}
    for pattern in ("references/*.md", "scripts/*.py"):
        for file in sorted(root.glob(pattern)):
            if file.name in {"run_checks.py", "fitz_compat.py"}:
                continue
            rel = file.relative_to(root).as_posix()
            if rel not in covered:
                out.append(finding("warn", f"{rel} 没有对应的路由意图"))
    return out


def check_evals(root: Path) -> list[dict]:
    out: list[dict] = []
    path = root / "evals/cases.jsonl"
    seen: set[str] = set()
    for no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            out.append(finding("error", f"cases.jsonl 第 {no} 行无法解析：{exc}"))
            continue
        cid = str(case.get("id") or "")
        case_type = str(case.get("type") or "")
        if not cid:
            out.append(finding("error", f"cases.jsonl 第 {no} 行缺 id"))
        elif cid in seen:
            out.append(finding("error", f"cases.jsonl 第 {no} 行 id={cid} 重复"))
        seen.add(cid)
        if case_type not in EVAL_TYPES:
            out.append(finding("error", f"{cid} type={case_type or '空'} 不在 {sorted(EVAL_TYPES)}"))
            continue
        if case_type in {"trigger", "non_trigger"} and not isinstance(case.get("expect_skill"), bool):
            out.append(finding("error", f"{cid} 缺布尔字段 expect_skill"))
        if case_type == "behavior" and not str(case.get("expect") or "").strip():
            out.append(finding("error", f"{cid} behavior 用例缺 expect"))
        if not str(case.get("input") or "").strip():
            out.append(finding("error", f"{cid} 缺 input"))
    if len(seen) < 10:
        out.append(finding("warn", f"只有 {len(seen)} 条评测用例，覆盖不足"))
    return out


def check_compile(root: Path) -> list[dict]:
    out: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for path in sorted(root.glob("scripts/*.py")):
            try:
                py_compile.compile(
                    str(path), cfile=str(Path(tmp) / f"{path.stem}.pyc"), doraise=True
                )
            except py_compile.PyCompileError as exc:
                out.append(finding("error", f"{path.name} 编译失败：{exc.msg.strip()}"))
    return out


def check_index_roundtrip(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/kb_index.py"
    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        kb.mkdir()
        (kb / "LEC-001-示例.md").write_text(
            "# 导数与极限\n\n## 洛必达法则\n\n洛必达法则要求分子分母同时趋于零且导数之比极限存在。\n",
            encoding="utf-8",
        )
        (kb / "LEC-002-例题.md").write_text(
            "# 求导\n\n## 例题 2.1 链式法则\n\n复合函数求导要用链式法则。\n",
            encoding="utf-8",
        )
        (kb / "LEC-003-四类.md").write_text(
            "# 四类切块样例\n\n"
            "## 定理 3.1 洛必达法则\n\n洛必达法则要求分子分母同时趋于零。\n\n"
            "## 步骤 求极限流程\n\n先判型，再检查条件，最后验算。\n\n"
            "## 例题 3.2 等价替换\n\n用等价无穷小替换简化计算。\n\n"
            "## 陷阱 直接替换\n\n减法结构不能直接等价替换。\n",
            encoding="utf-8",
        )
        db = Path(tmp) / "index.db"
        build = subprocess.run(
            [sys.executable, str(script), "build", str(kb), "--db", str(db)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if build.returncode != 0 or "chunks=" not in build.stdout:
            out.append(finding("error", f"建索引失败：{build.stderr.strip() or build.stdout.strip()}"))
            return out
        search = subprocess.run(
            [sys.executable, str(script), "search", str(db), "洛必达", "--top", "3"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if search.returncode != 0 or "LEC-001" not in search.stdout:
            out.append(finding("error", f"检索中文子串失败：{search.stdout.strip() or search.stderr.strip()}"))
        kind_search = subprocess.run(
            [
                sys.executable, str(script), "search", str(db), "链式法则",
                "--top", "3", "--kind", "例题",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if kind_search.returncode != 0 or "LEC-002" not in kind_search.stdout:
            out.append(finding(
                "error",
                f"按 kind 过滤失败：{kind_search.stdout.strip() or kind_search.stderr.strip()}",
            ))
        wrong_kind = subprocess.run(
            [sys.executable, str(script), "search", str(db), "洛必达", "--top", "3", "--kind", "例题"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if wrong_kind.returncode != 1 or "LEC-001" in wrong_kind.stdout:
            out.append(finding(
                "error",
                f"kind 过滤未生效（定理块出现在例题结果里）：{wrong_kind.stdout.strip()}",
            ))
        for kind, query in (
            ("定理", "洛必达"),
            ("步骤", "先判型"),
            ("例题", "等价无穷小"),
            ("陷阱", "减法结构"),
        ):
            proc = subprocess.run(
                [sys.executable, str(script), "search", str(db), query,
                 "--top", "3", "--kind", kind],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0 or f"[{kind}]" not in proc.stdout:
                out.append(finding(
                    "error",
                    f"四类切块过滤失败（kind={kind}）：{proc.stdout.strip() or proc.stderr.strip()}",
                ))
        queries = Path(tmp) / "queries.jsonl"
        queries.write_text(
            '{"query": "洛必达", "expect": ["LEC-001"]}\n'
            '{"query": "链式法则", "expect": ["LEC-002"]}\n',
            encoding="utf-8",
        )
        evaluation = subprocess.run(
            [sys.executable, str(script), "eval", str(db), str(queries), "--top", "3"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if evaluation.returncode != 0 or "hit@3=2/2" not in evaluation.stdout:
            out.append(finding(
                "error",
                f"检索评估失败：{evaluation.stdout.strip() or evaluation.stderr.strip()}",
            ))
    return out


def check_step_check(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/step_check.py"
    cases = [
        ("正确链", ["--steps", "x+x", "2*x", "--var", "x"], 0),
        ("错误链", ["--steps", "x+x", "3*x", "--var", "x"], 2),
    ]
    for label, args, expected in cases:
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode == 3:
            return [finding("info", "未安装 sympy，跳过 step_check")]
        if proc.returncode != expected:
            out.append(finding(
                "error",
                f"{label}返回 {proc.returncode}（期望 {expected}）："
                f"{proc.stdout.strip() or proc.stderr.strip()}",
            ))
    return out


def check_explain_lint(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/explain_lint.py"
    cases = [
        ("讲题稿好样例", "evals/fixtures/explain-solution-good.md", "solution"),
        ("追问稿好样例", "evals/fixtures/explain-followup-good.md", "followup"),
        ("多解稿好样例", "evals/fixtures/explain-multi-good.md", "multi"),
    ]
    for label, rel, level in cases:
        proc = subprocess.run(
            [sys.executable, str(script), str(root / rel), "--level", level, "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            out.append(finding(
                "error",
                f"{label}返回 {proc.returncode}："
                f"{proc.stdout.strip() or proc.stderr.strip()}",
            ))
            continue
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out.append(finding("error", f"{label}输出不是 JSON：{proc.stdout[:120]}"))
            continue
        if not data.get("ok"):
            out.append(finding("error", f"{label}未通过：{data.get('missing')}"))

    negative = [
        ("缺易错点的讲题稿", "evals/fixtures/explain-solution-good.md", "solution",
         r"##\s*[^\n]*易错点[^\n]*\n.*?(?=##\s*[^\n]*一句话)", "易错点"),
        ("缺视角的追问稿", "evals/fixtures/explain-followup-good.md", "followup",
         r"##\s*视角\s*2.*?(?=##\s*数值验证)", "视角"),
        ("缺来源的多解稿", "evals/fixtures/explain-multi-good.md", "multi",
         r"##\s*[^\n]*来源[^\n]*\n.*?(?=##\s*[^\n]*一句话)", "来源"),
        ("缺验算的多解稿", "evals/fixtures/explain-multi-good.md", "multi",
         r"##\s*[^\n]*验算[^\n]*\n.*?(?=##\s*[^\n]*来源)", "验算"),
    ]
    for label, rel, level, pattern, needle in negative:
        text = (root / rel).read_text(encoding="utf-8")
        broken = re.sub(pattern, "", text, flags=re.S)
        if broken == text:
            out.append(finding("error", f"{label}负样例构造失败：模式未命中"))
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.md"
            path.write_text(broken, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script), str(path), "--level", level, "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
        if proc.returncode != 1:
            out.append(finding("error", f"{label}返回 {proc.returncode}（期望 1）"))
            continue
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            out.append(finding("error", f"{label}输出不是 JSON：{proc.stdout[:120]}"))
            continue
        if not any(needle in item for item in data.get("missing") or []):
            out.append(finding(
                "error", f"{label}未报告「{needle}」：{data.get('missing')}",
            ))
    return out


def check_solution_research(root: Path) -> list[dict]:
    out: list[dict] = []
    doc = root / "references/solution-research.md"
    if not doc.is_file():
        return [finding("error", "缺文件：references/solution-research.md")]
    text = doc.read_text(encoding="utf-8", errors="replace")
    for needle in ("什么时候检索", "检索顺序", "多解对比", "未外部检索",
                   "explain_lint.py", "--level multi", "web_search.py",
                   "--include-domains", "apiKeyUsed=false",
                   "StackExchange 不做首选", "先译成英文关键词"):
        if needle not in text:
            out.append(finding("error", f"solution-research.md 缺「{needle}」"))
    skill = root / "SKILL.md"
    if not skill.is_file():
        return out + [finding("error", "缺文件：SKILL.md")]
    skill_text = skill.read_text(encoding="utf-8", errors="replace")
    for needle in ("多解必须验算", "references/solution-research.md",
                   "检索先跑脚本", "scripts/web_search.py", "StackExchange 必须先用英文关键词"):
        if needle not in skill_text:
            out.append(finding("error", f"SKILL.md 缺「{needle}」"))
    return out


def check_web_search(root: Path) -> list[dict]:
    """零 key 检索：解析、域名过滤、字符预算、反爬与无 key 声明。"""
    out: list[dict] = []
    script = root / "scripts/web_search.py"
    brave = root / "evals/fixtures/search-brave.html"
    yahoo = root / "evals/fixtures/search-yahoo.html"
    challenge = root / "evals/fixtures/search-challenge.html"

    def run(extra: list[str]) -> tuple[int, dict | None, str]:
        proc = subprocess.run(
            [sys.executable, str(script), *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            return proc.returncode, json.loads(proc.stdout), ""
        except json.JSONDecodeError:
            return proc.returncode, None, (proc.stdout or proc.stderr).strip()[:160]

    cases = [
        ("Brave 解析", ["q", "--engine", "brave", "--fixture-brave", str(brave),
                       "--max-results", "3"], 0, "brave-html", 3),
        ("Yahoo 解析", ["q", "--engine", "yahoo", "--fixture-yahoo", str(yahoo),
                        "--max-results", "3"], 0, "yahoo-html", 2),
        ("域名过滤", ["q", "--engine", "brave", "--fixture-brave", str(brave),
                      "--include-domains", "bilibili.com"], 0, "brave-html", 1),
        ("字符预算", ["q", "--engine", "brave", "--fixture-brave", str(brave),
                      "--max-chars", "100"], 0, "brave-html", 1),
    ]
    for label, extra, expected, provider, minimum in cases:
        code, data, err = run(extra)
        if data is None:
            out.append(finding("error", f"{label}输出不是 JSON：{err}"))
            continue
        if code != expected:
            out.append(finding("error", f"{label}返回 {code}（期望 {expected}）：{data.get('firstFailureReason')}"))
            continue
        if data.get("provider") != provider:
            out.append(finding("error", f"{label} provider={data.get('provider')}（期望 {provider}）"))
        if len(data.get("results") or []) < minimum:
            out.append(finding("error", f"{label}结果数 {len(data.get('results') or [])} < {minimum}"))
        if data.get("apiKeyUsed") is not False:
            out.append(finding("error", f"{label} apiKeyUsed 不是 false"))
        if data.get("resultChars", 0) > data.get("outputBudgetChars", 0):
            out.append(finding("error", f"{label} resultChars 超出预算"))
        if label == "Yahoo 解析":
            urls = [r.get("url", "") for r in data.get("results") or []]
            if any(u.startswith("https://r.search.yahoo.com") for u in urls):
                out.append(finding("error", "Yahoo 结果未解出真实 URL"))
            if not any("bilibili.com" in u for u in urls):
                out.append(finding("error", "Yahoo 结果缺 bilibili.com"))
        if label == "域名过滤":
            urls = [r.get("url", "") for r in data.get("results") or []]
            if not urls or any("bilibili.com" not in u for u in urls):
                out.append(finding("error", f"域名过滤混入站外结果：{urls}"))

    # 正常结果正文出现 captcha 字样不应整次判 blocked
    noisy = Path(tempfile.mkdtemp()) / "brave-noisy.html"
    noisy.write_text(
        brave.read_text(encoding="utf-8", errors="replace").replace(
            "Asymptotic expansions for trigonometric limits",
            "Bypassing captcha walls: asymptotic expansions",
        ),
        encoding="utf-8",
    )
    code, data, err = run(["q", "--engine", "brave", "--fixture-brave", str(noisy),
                           "--max-results", "3"])
    if data is None or code != 0 or len(data.get("results") or []) != 3:
        out.append(finding("error", f"结果正文含 captcha 时被误判 blocked：code={code} {err}"))

    code, data, err = run(["q", "--engine", "brave", "--fixture-brave", str(challenge)])
    if data is None:
        out.append(finding("error", f"反爬样例输出不是 JSON：{err}"))
    elif code != 2 or "challenge-detected" not in (data.get("firstFailureReason") or ""):
        out.append(finding(
            "error",
            f"反爬样例未按 challenge-detected 退出 2：code={code} reason={data.get('firstFailureReason')}",
        ))
    return out


def check_obsidian_cards(root: Path) -> list[dict]:
    """Obsidian 每卡一文件：语法、幂等、文件名稳定、孤儿只报不删。"""
    out: list[dict] = []
    script = root / "scripts/export_cards.py"
    template = root / "知识库模板"
    if not script.is_file():
        return [finding("error", "缺文件：scripts/export_cards.py")]
    if not template.is_dir():
        return [finding("error", "缺目录：知识库模板")]

    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        shutil.copytree(template, kb)

        def run(extra: list[str]) -> tuple[int, dict | None, str]:
            proc = subprocess.run(
                [sys.executable, str(script), "--kb", str(kb), "--json", *extra],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            try:
                return proc.returncode, json.loads(proc.stdout), ""
            except json.JSONDecodeError:
                return proc.returncode, None, (proc.stdout or proc.stderr).strip()[:160]

        code, data, err = run([])
        if data is None:
            return out + [finding("error", f"export_cards 输出不是 JSON：{err}")]
        if code != 0 or not data.get("created"):
            out.append(finding("error", f"首次导出未建卡：code={code} raw={err}"))
        cards = sorted((kb / "notes/cards").glob("CARD-*.md"))
        if len(cards) != 1:
            out.append(finding("error", f"每卡一文件不成立：找到 {len(cards)} 个文件"))
        else:
            text = cards[0].read_text(encoding="utf-8")
            for needle in ("id: CARD-0001", "\n?\n", "#review", "有理化"):
                if needle not in text:
                    out.append(finding("error", f"卡片缺「{needle.strip() or needle}」"))
            if "views" in cards[0].as_posix():
                out.append(finding("error", "卡片被写进 views/，重建会丢复习进度"))

        first_name = cards[0].name if cards else ""
        code, data, err = run([])
        if data is None:
            out.append(finding("error", f"重跑输出不是 JSON：{err}"))
        else:
            if data.get("created"):
                out.append(finding("error", "重跑又建了新文件，导出不幂等"))
            if int(data.get("unchanged") or 0) < 1:
                out.append(finding("error", "重跑未命中 unchanged，内容在抖动"))
        if cards and len(list((kb / "notes/cards").glob("CARD-*.md"))) != 1:
            out.append(finding("error", "重跑后卡片文件数量变化"))

        cards_file = kb / "records/cards.jsonl"
        rows = [json.loads(line) for line in cards_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows[0]["title"] = "改名后的标题"
        cards_file.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
        code, data, err = run([])
        renamed = sorted((kb / "notes/cards").glob("CARD-*.md"))
        if len(renamed) != 1 or renamed[0].name != first_name:
            out.append(finding("error", "标题变化后文件名变了，Obsidian 复习进度会清零"))
        elif "改名后的标题" not in renamed[0].read_text(encoding="utf-8"):
            out.append(finding("error", "标题变化后内容没更新"))

        cards_file.write_text("", encoding="utf-8")
        code, data, err = run([])
        if data is None:
            out.append(finding("error", f"空卡片库输出不是 JSON：{err}"))
        elif not data.get("orphans"):
            out.append(finding("error", "源记录删除后未报孤儿卡片"))
        if renamed and not renamed[0].is_file():
            out.append(finding("error", "孤儿卡片被自动删除了，应只报不删"))

    for rel, needles in (
        ("references/knowledge-base.md", ("export_cards.py", "每张卡一个文件", "reviews.jsonl")),
        ("SKILL.md", ("scripts/export_cards.py",)),
    ):
        path = root / rel
        if not path.is_file():
            out.append(finding("error", f"缺文件：{rel}"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle not in text:
                out.append(finding("error", f"{rel} 缺「{needle}」"))
    return out


def check_session_digest(root: Path) -> list[dict]:
    """开场摘要：字数上限、截断标记、只读指针与记录、缺库退出 2。"""
    out: list[dict] = []
    script = root / "scripts/session_digest.py"
    template = root / "知识库模板"
    if not script.is_file():
        return [finding("error", "缺文件：scripts/session_digest.py")]
    if not template.is_dir():
        return [finding("error", "缺目录：知识库模板")]

    def run(kb: Path, extra: list[str]) -> tuple[int, dict | None, str]:
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--json", *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            return proc.returncode, json.loads(proc.stdout), ""
        except json.JSONDecodeError:
            return proc.returncode, None, (proc.stdout or proc.stderr).strip()[:160]

    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        shutil.copytree(template, kb)

        code, data, err = run(kb, [])
        if data is None:
            return out + [finding("error", f"session_digest 输出不是 JSON：{err}")]
        if code != 0:
            out.append(finding("error", f"摘要返回 {code}"))
        digest = data.get("digest") or ""
        if int(data.get("chars") or 0) != len(digest):
            out.append(finding("error", f"chars={data.get('chars')} 与实际 {len(digest)} 字不一致"))
        if int(data.get("chars") or 0) > 2000:
            out.append(finding("error", f"默认摘要 {data.get('chars')} 字，超过 2000"))
        for needle in ("进度指针", "复习", "未闭环错题"):
            if needle not in digest:
                out.append(finding("error", f"摘要缺「{needle}」"))
        for rel in data.get("readFiles") or []:
            if not (rel == "progress.md" or rel.startswith("records/")):
                out.append(finding("error", f"摘要读了不该读的文件：{rel}"))
        if not data.get("readFiles"):
            out.append(finding("error", "摘要没有报告 readFiles"))

        code, data, err = run(kb, ["--max-chars", "300"])
        if data is None:
            out.append(finding("error", f"截断模式输出不是 JSON：{err}"))
        else:
            if int(data.get("chars") or 0) > 300:
                out.append(finding("error", f"截断后 {data.get('chars')} 字，超过 300"))
            if int(data.get("chars") or 0) != len(data.get("digest") or ""):
                out.append(finding("error", "截断模式 chars 与 digest 长度不一致"))
            if not data.get("truncated"):
                out.append(finding("error", "超限时没有标 truncated"))
            if "已截断" not in (data.get("digest") or ""):
                out.append(finding("error", "截断输出没有「已截断」提示"))

    proc = subprocess.run(
        [sys.executable, str(script), "--kb", str(Path(tmp) / "missing")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 2:
        out.append(finding("error", f"缺学习库未退出 2：{proc.returncode}"))

    skill = root / "SKILL.md"
    if skill.is_file():
        text = skill.read_text(encoding="utf-8", errors="replace")
        if "scripts/session_digest.py" not in text:
            out.append(finding("error", "SKILL.md 缺 scripts/session_digest.py"))
    return out


def check_anki_export(root: Path) -> list[dict]:
    """Anki TSV：文件头、GUID 稳定、HTML 转义、重跑幂等、retired 默认跳过。"""
    out: list[dict] = []
    script = root / "scripts/export_anki.py"
    template = root / "知识库模板"
    if not script.is_file():
        return [finding("error", "缺文件：scripts/export_anki.py")]
    if not template.is_dir():
        return [finding("error", "缺目录：知识库模板")]

    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        shutil.copytree(template, kb)

        def run(extra: list[str]) -> tuple[int, dict | None, str]:
            proc = subprocess.run(
                [sys.executable, str(script), "--kb", str(kb), "--json", *extra],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            try:
                return proc.returncode, json.loads(proc.stdout), ""
            except json.JSONDecodeError:
                return proc.returncode, None, (proc.stdout or proc.stderr).strip()[:160]

        tsvi = kb / "views/anki-cards.tsv"
        code, data, err = run([])
        if data is None:
            return out + [finding("error", f"export_anki 输出不是 JSON：{err}")]
        if code != 0 or data.get("exported") != 1:
            out.append(finding("error", f"模板库导出异常：code={code} exported={data.get('exported')}"))
        if not tsvi.is_file():
            return out + [finding("error", "没有生成 views/anki-cards.tsv")]

        lines = tsvi.read_text(encoding="utf-8").splitlines()
        for header in ("#separator:tab", "#html:true", "#notetype column:5",
                       "#deck column:4", "#tags column:3", "#guid column:6"):
            if header not in lines[:8]:
                out.append(finding("error", f"缺文件头 {header}"))
        rows = [line for line in lines if line and not line.startswith("#")]
        if len(rows) != 1:
            out.append(finding("error", f"应有 1 行卡片，实际 {len(rows)}"))
        else:
            fields = rows[0].split("\t")
            if len(fields) != 6:
                out.append(finding("error", f"列数应为 6，实际 {len(fields)}"))
            if fields[-1] != "CARD-0001":
                out.append(finding("error", f"GUID 列不是 CARD-0001：{fields[-1]}"))
            if "<b>方法</b>" not in fields[1]:
                out.append(finding("error", "背面的步骤/方法没有渲染成 HTML"))

        cards_file = kb / "records/cards.jsonl"
        base = [json.loads(line) for line in cards_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        base.append({
            "id": "CARD-0002", "type": "陷阱", "title": "<b>转义</b>\t测试",
            "signal": "含 <tag> 与\t制表符", "method": "第一行\n第二行",
            "steps": ["步骤 A", "步骤 B"], "boundary": "无", "status": "hypothesis",
        })
        base.append({
            "id": "CARD-0003", "type": "定义", "title": "退役卡",
            "method": "旧", "status": "retired",
        })
        cards_file.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in base) + "\n", encoding="utf-8"
        )
        code, data, err = run([])
        if data is None or code != 0:
            out.append(finding("error", f"追加卡片后导出失败：{err}"))
        else:
            if data.get("exported") != 2:
                out.append(finding("error", f"默认应导出 2 张，实际 {data.get('exported')}"))
            if data.get("retiredSkipped") != 1:
                out.append(finding("error", "retired 卡片没有被默认跳过"))
        rows = [line for line in tsvi.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")]
        if len(rows) != 2:
            out.append(finding("error", f"retired 过滤后应有 2 行，实际 {len(rows)}"))
        else:
            second = rows[1].split("\t")
            if len(second) != 6:
                out.append(finding("error", "含制表符的标题破坏了列数"))
            if "<b>转义</b>" in rows[1]:
                out.append(finding("error", "标题里的 HTML 没被转义"))
            if "&lt;b&gt;转义&lt;/b&gt;" not in rows[1]:
                out.append(finding("error", "标题转义结果不对"))
            if "\n" in rows[1] or "\r" in rows[1]:
                out.append(finding("error", "字段里残留换行"))

        before = tsvi.read_text(encoding="utf-8")
        code, data, err = run([])
        if tsvi.read_text(encoding="utf-8") != before:
            out.append(finding("error", "重跑导出内容不一致，不幂等"))
        code, data, err = run(["--all"])
        if data is None or data.get("exported") != 3:
            out.append(finding("error", "--all 应导出 3 张（含 retired）"))

    for rel, needles in (
        ("references/knowledge-base.md", ("export_anki.py", "Anki")),
        ("SKILL.md", ("scripts/export_anki.py",)),
    ):
        path = root / rel
        if not path.is_file():
            out.append(finding("error", f"缺文件：{rel}"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle not in text:
                out.append(finding("error", f"{rel} 缺「{needle}」"))
    return out


def check_structure_map(root: Path) -> list[dict]:
    """结构地图：优先级分组、依赖边、未知区域、成环与门槛。"""
    out: list[dict] = []
    script = root / "scripts/structure_map.py"
    template = root / "知识库模板"
    if not script.is_file():
        return [finding("error", "缺文件：scripts/structure_map.py")]
    if not template.is_dir():
        return [finding("error", "缺目录：知识库模板")]

    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        shutil.copytree(template, kb)
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return out + [finding("error", f"structure_map 输出不是 JSON：{proc.stderr.strip()[:160]}")]
        if proc.returncode != 0 or data.get("nodes") != 6:
            out.append(finding("error", f"模板地图解析异常：code={proc.returncode} nodes={data.get('nodes')}"))
        if data.get("edges") != 5:
            out.append(finding("error", f"模板地图依赖边应为 5，实际 {data.get('edges')}"))
        if abs(float(data.get("mustRatio") or 0) - 1 / 3) > 0.01:
            out.append(finding("error", f"模板必掌握占比应约 33%，实际 {data.get('mustRatio')}"))
        if data.get("violations"):
            out.append(finding("error", f"模板地图违反自身门槛：{data.get('violations')}"))
        rendered = kb / "views/结构地图.md"
        if not rendered.is_file():
            out.append(finding("error", "没有生成 views/结构地图.md"))
        else:
            text = rendered.read_text(encoding="utf-8")
            for needle in ("mermaid", "mindmap", "必掌握", "依赖关系", "未知区域", "极限四则运算"):
                if needle not in text:
                    out.append(finding("error", f"结构地图缺「{needle}」"))

        bad = Path(tmp) / "bad"
        (bad / "views").mkdir(parents=True)
        (bad / "01-知识地图.md").write_text(
            "# 知识地图\n\n| 知识点 | 考纲要求 | 前置 | 状态 | 证据 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| A | 掌握 | B | L1 | LEC-001 |\n"
            "| B | 掌握 | A | L1 | LEC-001 |\n"
            "| C | 掌握 | 外部前置 | L1 | LEC-001 |\n"
            "| D | 掌握 | 无 | L1 | LEC-001 |\n"
            "| E | 掌握 | 无 | L1 | LEC-001 |\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(bad), "--strict", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = {}
        if proc.returncode != 1:
            out.append(finding("error", f"成环地图应退出 1，实际 {proc.returncode}"))
        if not data.get("cycles"):
            out.append(finding("error", "成环地图没有报 cycles"))
        if not data.get("violations"):
            out.append(finding("error", "--strict 下门槛违规没有进 violations"))
        if not data.get("unresolved"):
            out.append(finding("error", "外部前置没有被列进 unresolved"))

        dup = Path(tmp) / "dup"
        (dup / "views").mkdir(parents=True)
        (dup / "01-知识地图.md").write_text(
            "# 知识地图\n\n| 知识点 | 考纲要求 | 前置 | 状态 | 证据 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| A | 掌握 | B | L1 | LEC-001 |\n"
            "| A | 掌握 | C | L1 | LEC-001 |\n"
            "| B | 掌握 | A | L1 | LEC-001 |\n"
            "| C | 掌握 | 无 | L1 | LEC-001 |\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(dup), "--strict", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = {}
        if proc.returncode != 1:
            out.append(finding("error", f"重复行掩盖的环应退出 1，实际 {proc.returncode}"))
        if not data.get("cycles"):
            out.append(finding("error", "重复知识点合并后仍漏掉了环"))
        if data.get("duplicates") != ["A"]:
            out.append(finding("error", f"重复知识点没有报 duplicates：{data.get('duplicates')}"))

        deep = Path(tmp) / "deep"
        (deep / "views").mkdir(parents=True)
        deep_lines = ["# 知识地图", "", "| 知识点 | 考纲要求 | 前置 | 状态 | 证据 |",
                      "| --- | --- | --- | --- | --- |"]
        for i in range(1200, 0, -1):
            pre = "无" if i == 1 else f"N{i - 1}"
            deep_lines.append(f"| N{i} | 掌握 | {pre} | L1 | E{i} |")
        (deep / "01-知识地图.md").write_text("\n".join(deep_lines) + "\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(deep), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            out.append(finding("error", f"1200 层依赖链应正常退出，实际 {proc.returncode}"))
        if "RecursionError" in proc.stderr:
            out.append(finding("error", "深依赖链触发 RecursionError"))
        else:
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = {}
            if data.get("cycles"):
                out.append(finding("error", "无环深链被误判成环"))

    for rel, needles in (
        ("references/knowledge-base.md", ("structure_map.py", "结构地图")),
        ("SKILL.md", ("scripts/structure_map.py",)),
    ):
        path = root / rel
        if not path.is_file():
            out.append(finding("error", f"缺文件：{rel}"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle not in text:
                out.append(finding("error", f"{rel} 缺「{needle}」"))
    return out


def check_cheatsheet(root: Path) -> list[dict]:
    """一页速查表：四区齐全、定义不入表、A4 打印、缺易混与陷阱退出 1。"""
    out: list[dict] = []
    script = root / "scripts/cheatsheet.py"
    template = root / "知识库模板"
    if not script.is_file():
        return [finding("error", "缺文件：scripts/cheatsheet.py")]
    if not template.is_dir():
        return [finding("error", "缺目录：知识库模板")]

    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        shutil.copytree(template, kb)
        cards = kb / "records/cards.jsonl"
        rows = [json.loads(line) for line in cards.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows.append({
            "id": "CARD-0099", "type": "定义", "title": "定义不该进速查表",
            "method": "原样复述定义", "status": "verified",
        })
        cards.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return out + [finding("error", f"cheatsheet 输出不是 JSON：{proc.stderr.strip()[:160]}")]
        if proc.returncode != 0 or not data.get("gate"):
            out.append(finding("error", f"模板速查表未通过：code={proc.returncode} gate={data.get('gate')}"))
        md = kb / "views/速查表.md"
        html_file = kb / "views/速查表.html"
        if not md.is_file() or not html_file.is_file():
            out.append(finding("error", "没有生成速查表 md/html"))
        else:
            text = md.read_text(encoding="utf-8")
            for needle in ("核心公式与条件", "易混对照", "识别信号", "陷阱清单"):
                if needle not in text:
                    out.append(finding("error", f"速查表缺区块「{needle}」"))
            sections: dict[str, str] = {}
            current = ""
            for line in text.splitlines():
                if line.startswith("## "):
                    current = line[3:].strip()
                elif current:
                    sections[current] = sections.get(current, "") + line + "\n"
            for header, needle in (
                ("一、核心公式与条件", "根号内为负"),
                ("二、易混对照", "有理化的时机"),
                ("三、识别信号", "分子分母含根号"),
                ("四、陷阱清单", "ERR-0001"),
            ):
                if needle not in sections.get(header, ""):
                    out.append(finding("error", f"速查表「{header}」缺少模板内容「{needle}」"))
            if "定义不该进速查表" in text:
                out.append(finding("error", "定义卡被收进速查表"))
            page = html_file.read_text(encoding="utf-8")
            if "@page" not in page or "A4" not in page:
                out.append(finding("error", "速查表 HTML 没有 A4 打印样式"))

        empty = Path(tmp) / "empty"
        (empty / "records").mkdir(parents=True)
        for name in ("cards.jsonl", "errors.jsonl", "confusions.jsonl"):
            (empty / "records" / name).write_text("", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(empty), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 1:
            out.append(finding("error", f"空记录应退出 1，实际 {proc.returncode}"))
        if "不合格" not in proc.stderr:
            out.append(finding("error", "空记录没有给出「不合格」原因"))

    for rel, needles in (
        ("references/knowledge-base.md", ("cheatsheet.py", "速查表")),
        ("SKILL.md", ("scripts/cheatsheet.py",)),
    ):
        path = root / rel
        if not path.is_file():
            out.append(finding("error", f"缺文件：{rel}"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle not in text:
                out.append(finding("error", f"{rel} 缺「{needle}」"))
    return out


def check_fitz_compat(root: Path) -> list[dict]:
    """PyMuPDF 统一走兼容层：1.28 的 import fitz 横幅不得污染 JSON stdout。"""
    out: list[dict] = []
    helper = root / "scripts/fitz_compat.py"
    if not helper.is_file():
        return [finding("error", "缺文件：scripts/fitz_compat.py")]
    text = helper.read_text(encoding="utf-8", errors="replace")
    for needle in ("redirect_stdout", "import pymupdf", "import fitz"):
        if needle not in text:
            out.append(finding("error", f"fitz_compat.py 缺「{needle}」"))

    direct = re.compile(r"^\s*import (?:fitz|pymupdf)\b")
    for path in sorted((root / "scripts").glob("*.py")):
        if path.name == "fitz_compat.py":
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if direct.match(line):
                out.append(finding(
                    "error",
                    f"{path.name}:{no} 直接 import fitz/pymupdf，应改为 from fitz_compat import fitz",
                ))

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(root / "scripts"), env.get("PYTHONPATH", "")])
    probe = "from fitz_compat import fitz; print(fitz.NAME)"
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp)
        (fake / "pymupdf.py").write_text(
            'print("PyMuPDF 1.28 deprecation banner")\nNAME = "canonical"\n',
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, cwd=str(fake),
        )
        if proc.returncode != 0 or proc.stdout.strip() != "canonical":
            out.append(finding("error", f"canonical 导入未静默：stdout={proc.stdout.strip()[:120]}"))
        if "banner" in proc.stdout:
            out.append(finding("error", "pymupdf 的导入横幅污染了 stdout"))

        (fake / "pymupdf.py").write_text('raise ImportError("no pymupdf")\n', encoding="utf-8")
        (fake / "fitz.py").write_text(
            'print("legacy fitz deprecation banner")\nNAME = "legacy"\n',
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, cwd=str(fake),
        )
        if proc.returncode != 0 or proc.stdout.strip() != "legacy":
            out.append(finding("error", f"旧版回退未静默：stdout={proc.stdout.strip()[:120]}"))
        if "banner" in proc.stdout:
            out.append(finding("error", "旧版 fitz 的导入横幅污染了 stdout"))
    return out


PRIVACY_RULES = [
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("OpenAI 风格 key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("硬编码密钥", re.compile(
        r"(?i)\b(api[_-]?key|secret|password|access[_-]?token)\b\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']")),
    ("本机绝对路径", re.compile(
        r"(?i)\b[A-Z]:[\\/](?:Users|Documents(?: and Settings)?|Work|Projects|Desktop)[\\/]([^\\/\s\"'`]+)"
        r"|(?<![\w.])/(?:home|Users)/([^/\s\"'`]+)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]
PRIVACY_SKIP_DIRS = {".git", ".index", "__pycache__", ".venv", "node_modules", "学习库"}
PRIVACY_ALLOW_EMAIL_DOMAINS = ("users.noreply.github.com", "noreply.github.com",
                               "example.com", "example.org", "example.net")


def check_privacy(root: Path) -> list[dict]:
    """公开仓库前扫描：密钥、私钥、本机绝对路径与个人邮箱。"""
    out: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if PRIVACY_SKIP_DIRS & set(path.relative_to(root).parts):
            continue
        if path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in PRIVACY_RULES:
                for match in pattern.finditer(line):
                    hit = match.group(0)
                    if label == "本机绝对路径":
                        segment = next((g for g in match.groups() if g), "")
                        if ("<" in segment or "%" in segment
                                or segment.lower() in {"you", "user", "username", "name", "alice"}):
                            continue
                    if label == "邮箱":
                        domain = hit.rsplit("@", 1)[-1].lower()
                        if domain.endswith(PRIVACY_ALLOW_EMAIL_DOMAINS):
                            continue
                    out.append(finding("error", f"{label}：{rel}:{lineno}"))
    return out


def check_render_rules(root: Path) -> list[dict]:
    """聊天窗按 CommonMark 渲染，不解析 LaTeX；三份文件必须写清这一点。"""
    out: list[dict] = []
    expectations = [
        ("references/output-contract.md", ("## 对话输出", "CommonMark", "不解析", "Unicode", "代码块")),
        ("subjects/calculus/render.md", ("对话回复", "CommonMark", "Unicode")),
        ("SKILL.md", ("对话公式用 Unicode", "CommonMark")),
    ]
    for rel, needles in expectations:
        path = root / rel
        if not path.is_file():
            out.append(finding("error", f"缺文件：{rel}"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle not in text:
                out.append(finding("error", f"{rel} 缺渲染规则「{needle}」"))

    contract = root / "references/output-contract.md"
    if not contract.is_file():
        return out
    text = contract.read_text(encoding="utf-8", errors="replace")
    start = text.find("## 对话输出")
    end = text.find("\n## ", start + 1) if start >= 0 else -1
    section = text[start:end if end > 0 else len(text)] if start >= 0 else ""
    if not section:
        return out
    prose = re.sub(r"(?ms)^```.*?^```", "", section)
    prose = re.sub(r"(?m)^[ \t]{4}.*$", "", prose)
    prose = re.sub(r"`[^`\n]*`", "", prose)
    if "$" in prose:
        out.append(finding(
            "error",
            "output-contract.md 的「对话输出」正文仍有裸 $：对话用 Unicode，LaTeX 只放代码块",
        ))
    return out


def check_kb_health(root: Path, kb: Path | None) -> list[dict]:
    if kb is None:
        return [finding("info", "未指定 --kb，跳过学习库体检")]
    report = kb_health.check(kb)
    return [finding(f["level"], f"[{f['code']}] {f['msg']}") for f in report.findings]


def check_capabilities(root: Path) -> list[dict]:
    out: list[dict] = []
    path = root / "references/capabilities.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [finding("error", f"capabilities.json 无法解析：{exc}")]
    caps = data.get("capabilities")
    if not isinstance(caps, list) or not caps:
        return [finding("error", "capabilities.json 缺 capabilities 数组")]
    seen: set[str] = set()
    for index, cap in enumerate(caps, 1):
        if not isinstance(cap, dict):
            out.append(finding("error", f"第 {index} 项不是对象"))
            continue
        cid = str(cap.get("id") or "")
        if not cid:
            out.append(finding("error", f"第 {index} 项缺 id"))
        elif cid in seen:
            out.append(finding("error", f"能力 id 重复：{cid}"))
        seen.add(cid)
        for key in ("name", "inputs", "outputs", "failure_modes"):
            value = cap.get(key)
            if not value or not isinstance(value, list if key != "name" else str):
                out.append(finding("error", f"{cid or index} 缺 {key} 或类型不对"))
        for key in ("entry", "reference"):
            rel = cap.get(key)
            if rel and not (root / str(rel)).exists():
                out.append(finding("error", f"{cid} 的 {key}={rel} 不存在"))
    if len(caps) < 8:
        out.append(finding("warn", f"能力契约只有 {len(caps)} 项，覆盖偏少"))
    return out


def check_retrieval_eval(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/kb_index.py"
    queries = root / "evals/retrieval.jsonl"
    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        kb.mkdir()
        for source in sorted((root / "subjects/calculus").glob("*.md")):
            (kb / source.name).write_text(
                source.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
            )
        db = Path(tmp) / "index.db"
        proc = subprocess.run(
            [sys.executable, str(script), "build", str(kb), "--db", str(db)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            return [finding("error", f"真实资料建索引失败：{proc.stderr.strip() or proc.stdout.strip()}")]
        for mode in ("hybrid", "bm25", "vector"):
            run = subprocess.run(
                [
                    sys.executable, str(script), "eval", str(db), str(queries),
                    "--top", "5", "--mode", mode,
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            summary = re.search(r"hit@5=(\d+)/(\d+)", run.stdout)
            if not summary:
                out.append(finding(
                    "error", f"{mode} 检索评估失败：{run.stdout.strip() or run.stderr.strip()}"
                ))
                continue
            hits, total = int(summary.group(1)), int(summary.group(2))
            # eval 在未全中时返回 1 是预期信号，只有 hybrid 要求全中。
            if mode == "hybrid" and hits != total:
                out.append(finding(
                    "error", f"hybrid hit@5={hits}/{total}，未达全中基线；先查切块与别名"
                ))
            elif mode == "vector" and hits * 2 < total:
                out.append(finding(
                    "warn", f"vector hit@5={hits}/{total}，字符 bigram 覆盖偏低"
                ))
    return out


def check_intake_gate(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/intake_check.py"
    fixtures = root / "evals/fixtures"
    cases = [
        ("paper-good.md", ["--expect-count", "3", "--expect-total", "30"], 0, ()),
        ("paper-bad.md", [], 1, ("题号重复", "缺分值", "不一致")),
        ("questions-good.jsonl",
         ["--expect-count", "3", "--expect-total", "30", "--expect-types", "计算:3"], 0, ()),
        ("questions-bad.jsonl", [], 1, ("缺字段", "待核", "重复")),
        ("questions-schema.jsonl", [], 1,
         ("缺字段", "latex 必须是数组", "page_start 必须是", "page_end=2 小于")),
        ("questions-good.jsonl", ["--expect-types", "计算:2"], 1, ("题型分布",)),
        ("paper-good.md", ["--expect-types", "计算:3"], 1, ("没有 type 字段",)),
    ]
    for name, extra, expected, needles in cases:
        proc = subprocess.run(
            [sys.executable, str(script), str(fixtures / name), *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != expected:
            out.append(finding(
                "error",
                f"{name} 返回 {proc.returncode}（期望 {expected}）："
                f"{proc.stdout.strip() or proc.stderr.strip()}",
            ))
            continue
        text = proc.stdout
        absent = [needle for needle in needles if needle not in text]
        if absent:
            out.append(finding(
                "error", f"{name} 输出缺少预期提示 {absent}：{text.strip()}"
            ))
    return out


def check_pdf_probe(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/pdf_probe.py"
    try:
        from fitz_compat import fitz
    except ImportError:
        return [finding("info", "跳过 pdf_probe：未安装 PyMuPDF")]

    with tempfile.TemporaryDirectory() as tmp:
        text_pdf = Path(tmp) / "text.pdf"
        blank_pdf = Path(tmp) / "blank.pdf"
        for path, with_text in ((text_pdf, True), (blank_pdf, False)):
            doc = fitz.open()
            for _ in range(4):
                page = doc.new_page()
                if with_text:
                    for i in range(20):
                        page.insert_text(
                            (50, 60 + i * 24),
                            f"计算下列极限并写出完整步骤 第{i + 1}题 微积分期中试卷",
                            fontsize=11, fontname="china-s",
                        )
            doc.save(path)
            doc.close()

        cases = [
            (text_pdf, [], 0, "verdict=A"),
            (blank_pdf, [], 0, "verdict=B"),
            (Path(tmp) / "missing.pdf", [], 2, ""),
            (root / "SKILL.md", [], 2, ""),
            (text_pdf, ["--json"], 0, '"verdict": "A"'),
        ]
        for target, extra, expected, needle in cases:
            proc = subprocess.run(
                [sys.executable, str(script), str(target), *extra],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != expected:
                out.append(finding(
                    "error",
                    f"pdf_probe {target.name} {extra} 返回 {proc.returncode}（期望 {expected}）："
                    f"{proc.stdout.strip() or proc.stderr.strip()}",
                ))
            elif needle and needle not in proc.stdout:
                out.append(finding(
                    "error", f"pdf_probe {target.name} 输出缺少 {needle}：{proc.stdout.strip()}"
                ))
    return out


def check_paper_report(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/paper_report.py"
    fixtures = root / "evals/fixtures"
    good = fixtures / "questions-analyzed.jsonl"
    bad = fixtures / "questions-unclassified.jsonl"

    proc = subprocess.run(
        [sys.executable, str(script), str(good)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        out.append(finding("error", f"paper_report 好卷返回 {proc.returncode}：{proc.stderr.strip()}"))
    for needle in ("整卷报告 MID-2025A", "| 计算 | 3 | 24 |", "第1章 1.4",
                   "MID-2025A#1", "待核题", "| 3 | 0.85 |"):
        if needle not in proc.stdout:
            out.append(finding("error", f"paper_report 输出缺少 {needle}"))

    proc = subprocess.run(
        [sys.executable, str(script), str(bad)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 1 or "考点分值合计" not in proc.stdout:
        out.append(finding("error", f"paper_report 未归类卷应退出 1 并提示考点合计：{proc.returncode}"))

    proc = subprocess.run(
        [sys.executable, str(script), str(Path("nope") / "missing.jsonl")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 2:
        out.append(finding("error", f"paper_report 缺文件应退出 2，实际 {proc.returncode}"))

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "nested" / "PAPER-MID.md"
        proc = subprocess.run(
            [sys.executable, str(script), str(good), "--out", str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not target.is_file():
            out.append(finding("error", "paper_report --out 未生成报告"))
        elif not target.read_text(encoding="utf-8").startswith("<!-- generated by scripts/paper_report.py"):
            out.append(finding("error", "paper_report 产物缺生成标记"))
    return out


def check_weekly_report(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/weekly_report.py"

    def write_jsonl(path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        records = kb / "records"
        write_jsonl(records / "events.jsonl", [
            {"id": "EVT-000001", "ts": "2026-11-06T10:00:00+08:00",
             "stage": "lecture", "action": "lecture", "result": "ok"},
            {"id": "EVT-000002", "ts": "2026-11-07T10:00:00+08:00",
             "stage": "lecture", "action": "exit_ticket", "result": "pass"},
            {"id": "EVT-000003", "ts": "2026-10-01T10:00:00+08:00",
             "stage": "lecture", "action": "lecture", "result": "ok"},
        ])
        write_jsonl(records / "reviews.jsonl", [
            {"id": "REV-0001", "card_id": "CARD-0001", "ts": "2026-11-06",
             "rating": 3, "step_scores": [3, 2, 3], "due": "2026-11-13"},
            {"id": "REV-0002", "card_id": "CARD-0002", "ts": "2026-11-08",
             "rating": 2, "step_scores": [2, 2], "due": "2026-11-11"},
        ])
        write_jsonl(records / "errors.jsonl", [
            {"id": "ERR-0001", "ts": "2026-11-07", "concept": "无穷小比较",
             "count": 3, "first_seen": "2026-11-05", "status": "open"},
        ])
        write_jsonl(records / "confusions.jsonl", [
            {"id": "CONF-0001", "date": "2026-11-07", "tag": "无穷小的阶", "ask_count": 2},
        ])
        write_jsonl(records / "cards.jsonl", [
            {"id": "CARD-0001", "ts": "2026-11-06", "type": "步骤", "status": "hypothesis"},
        ])
        write_jsonl(records / "sources.jsonl", [
            {"id": "SRC-0001", "added_at": "2026-11-05"},
        ])

        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--end", "2026-11-08", "--days", "7"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            out.append(finding("error", f"weekly_report 返回 {proc.returncode}：{proc.stderr.strip()}"))
        for needle in ("学习周报 2026-11-02 ~ 2026-11-08", "事件 2；复习 2",
                       "退出票：通过 1 / 未通过 0", "最弱步骤：第 2 步（均分 2.00，2 次）",
                       "ERR-0001", "无穷小的阶 2 次", "平均 rating：2.5"):
            if needle not in proc.stdout:
                out.append(finding("error", f"weekly_report 输出缺少 {needle}"))

        empty = Path(tmp) / "empty"
        empty.mkdir()
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(empty), "--end", "2026-11-08"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or "窗口内没有任何记录" not in proc.stdout:
            out.append(finding("error", "weekly_report 空库未输出自检提示"))

        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(Path(tmp) / "nope")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"weekly_report 缺库应退出 2，实际 {proc.returncode}"))
    return out


def check_ocr_page(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/ocr_page.py"
    try:
        from fitz_compat import fitz
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return [finding("info", "跳过 ocr_page：未安装 PyMuPDF 或 RapidOCR")]

    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "page.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((60, 120), "CALCULUS EXAM 2025 PAGE 1", fontsize=22)
        doc.save(pdf)
        doc.close()

        cases = [
            (pdf, ["--page", "1", "--dpi", "150"], 0, "CALCULUS"),
            (pdf, ["--page", "99"], 2, "超出范围"),
            (root / "SKILL.md", [], 2, "不是 PDF"),
            (Path(tmp) / "missing.pdf", [], 2, "not a file"),
        ]
        for target, extra, expected, needle in cases:
            proc = subprocess.run(
                [sys.executable, str(script), str(target), *extra],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != expected:
                out.append(finding(
                    "error",
                    f"ocr_page {target.name} {extra} 返回 {proc.returncode}（期望 {expected}）："
                    f"{proc.stdout.strip() or proc.stderr.strip()}",
                ))
            elif needle and needle not in (proc.stdout + proc.stderr):
                out.append(finding("error", f"ocr_page {target.name} 输出缺少 {needle}"))
    return out


def check_slice_questions(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/slice_questions.py"
    fixtures = root / "evals/fixtures"
    with tempfile.TemporaryDirectory() as tmp:
        text = Path(tmp) / "page.txt"
        text.write_text(
            "1. 计算下列极限（8分）\nlim (sqrt(1+x)-1)/x\n"
            "2. 证明中值定理（10分）\n设函数在闭区间连续\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(script), str(text), "--paper", "TXT-2025"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or "questions=2" not in proc.stdout:
            out.append(finding(
                "error", f"slice_questions 文本输入失败：{proc.stdout.strip() or proc.stderr.strip()}"
            ))

        proc = subprocess.run(
            [sys.executable, str(script), str(fixtures / "paper-good.md"), "--paper", "MD-2025"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or "questions=3" not in proc.stdout:
            out.append(finding(
                "error", f"slice_questions Markdown 输入失败：{proc.stdout.strip() or proc.stderr.strip()}"
            ))

        draft = Path(tmp) / "draft.jsonl"
        proc = subprocess.run(
            [sys.executable, str(script), str(fixtures / "ocr-page.json"),
             "--paper", "MID-2025A", "--out", str(draft)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not draft.is_file():
            out.append(finding("error", f"slice_questions OCR 输入失败：{proc.stderr.strip()}"))
        else:
            rows = [json.loads(line) for line in draft.read_text(encoding="utf-8").splitlines()]
            if len(rows) != 3:
                out.append(finding("error", f"slice_questions 应切出 3 题，实际 {len(rows)}"))
            if len(rows) >= 2 and "待核" not in rows[1].get("flags", []):
                out.append(finding("error", "slice_questions 未把低置信行标「待核」"))
            if len(rows) >= 3 and rows[2].get("sub") != [1]:
                out.append(finding("error", "slice_questions 未记录小题号 sub=[1]"))
            if any(str(row.get("no")) == "3" and "3.14" in row.get("stem", "") and len(row["stem"]) < 20
                   for row in rows if row is not rows[2]):
                out.append(finding("error", "slice_questions 把小数 3.14 误切成独立题"))

        bad_json = Path(tmp) / "bad.json"
        bad_json.write_text('{"source": "x"}', encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script), str(bad_json)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"slice_questions 坏 JSON 应退出 2，实际 {proc.returncode}"))
        proc = subprocess.run(
            [sys.executable, str(script), str(Path(tmp) / "nope.txt")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"slice_questions 缺文件应退出 2，实际 {proc.returncode}"))
    return out


def check_chapter_diff(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/chapter_diff.py"
    fixtures = root / "evals/fixtures"
    proc = subprocess.run(
        [
            sys.executable, str(script),
            str(fixtures / "chapters-old.md"), str(fixtures / "chapters-new.md"),
            "--questions", str(fixtures / "questions-retired.jsonl"),
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        out.append(finding("error", f"chapter_diff 失败：{proc.stderr.strip() or proc.stdout.strip()}"))
    for needle in ("新增 1，删除 0，疑似改名 2", "2.3 高阶导数", "MID-2025A#2", "章节已改名"):
        if needle not in proc.stdout:
            out.append(finding("error", f"chapter_diff 输出缺少 {needle}"))
    if "MID-2025A#4" in proc.stdout:
        out.append(finding("error", "chapter_diff 误把未受影响题标为已退役"))

    with tempfile.TemporaryDirectory() as tmp:
        try:
            from fitz_compat import fitz

            for name, chapters in (
                ("old.pdf", [("第1章 极限", 1), ("第2章 导数", 2)]),
                ("new.pdf", [("第1章 极限", 1), ("第2章 导数与微分", 3)]),
            ):
                doc = fitz.open()
                for _ in range(3):
                    doc.new_page()
                doc.set_toc([[1, title, page] for title, page in chapters])
                doc.save(Path(tmp) / name)
                doc.close()
            proc = subprocess.run(
                [sys.executable, str(script), str(Path(tmp) / "old.pdf"), str(Path(tmp) / "new.pdf")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0 or "疑似改名 1" not in proc.stdout:
                out.append(finding(
                    "error", f"chapter_diff PDF 目录对比失败：{proc.stdout.strip() or proc.stderr.strip()}"
                ))
            elif "| 第2章 导数与微分 | 2 | 3 | +1 |" not in proc.stdout:
                out.append(finding("error", "chapter_diff PDF 未给页码偏移 +1"))
        except ImportError:
            out.append(finding("info", "跳过 chapter_diff 的 PDF 目录部分：未安装 PyMuPDF"))

        proc = subprocess.run(
            [sys.executable, str(script), str(Path(tmp) / "nope.md"), str(fixtures / "chapters-new.md")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"chapter_diff 缺文件应退出 2，实际 {proc.returncode}"))
    return out


def check_mock_paper(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/mock_paper.py"
    good = root / "evals/fixtures/questions-analyzed.jsonl"
    proc = subprocess.run(
        [sys.executable, str(script), str(good), "--title", "期中模拟"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        out.append(finding("error", f"mock_paper 失败：{proc.stderr.strip() or proc.stdout.strip()}"))
    for needle in ("有效题 3，总分 26", "| 计算 | 2 | 16 |", "M1", "判型（2.7 分）",
                   "待核题 1 道已剔除", "扣分点"):
        if needle not in proc.stdout:
            out.append(finding("error", f"mock_paper 输出缺少 {needle}"))

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "nested" / "MOCK.md"
        proc = subprocess.run(
            [sys.executable, str(script), str(good), "--out", str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not target.is_file():
            out.append(finding("error", "mock_paper --out 未生成蓝图"))
        elif not target.read_text(encoding="utf-8").startswith(
                "<!-- generated by scripts/mock_paper.py"):
            out.append(finding("error", "mock_paper 产物缺生成标记"))

        partial = Path(tmp) / "partial.jsonl"
        partial.write_text(
            '{"paper":"X","no":"1","type":"计算","points":8,"conf":0.95,"stem":"求极限"}\n',
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(script), str(partial)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or "缺 solution_skeleton" not in proc.stdout:
            out.append(finding("error", "mock_paper 缺骨架时未给警告"))

        broken = Path(tmp) / "broken.jsonl"
        broken.write_text(
            '{"paper":"X","no":"2","type":"计算","conf":0.95,"stem":"求极限"}\n',
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(script), str(broken)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 1 or "不能组卷" not in proc.stderr:
            out.append(finding("error", "mock_paper 缺分值应退出 1 并说明不能组卷"))

        proc = subprocess.run(
            [sys.executable, str(script), str(Path(tmp) / "nope.jsonl")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"mock_paper 缺文件应退出 2，实际 {proc.returncode}"))
    return out


def check_toc_extract(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/toc_extract.py"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            from fitz_compat import fitz
        except ImportError:
            return [finding("info", "跳过 toc_extract：未安装 PyMuPDF")]
        clean = Path(tmp) / "toc.pdf"
        doc = fitz.open()
        page = doc.new_page()
        lines = [
            "第一章 极限 .......... 1",
            "1.1 数列极限 .......... 1",
            "1.2 函数极限 .......... 3",
            "第二章 导数 .......... 5",
        ]
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontname="china-s", fontsize=12)
            y += 20
        doc.save(clean)
        doc.close()

        proc = subprocess.run(
            [sys.executable, str(script), str(clean), "--pages", "1", "--mode", "text", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            out.append(finding("error", f"toc_extract 失败：{proc.stderr.strip()}"))
        else:
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = {}
            tree = data.get("tree") or []
            if len(tree) != 4:
                out.append(finding("error", f"toc_extract 应解析 4 条目录，实际 {len(tree)}"))
            elif ([row["level"] for row in tree] != [1, 2, 2, 1]
                  or [row["page"] for row in tree] != [1, 1, 3, 5]
                  or "第一章" not in tree[0]["title"]):
                out.append(finding("error", f"toc_extract 层级/页码解析错误：{tree}"))
            if data.get("raw") is None:
                out.append(finding("error", "toc_extract 未输出 raw 原文，模型无法校对"))

        toc_json = Path(tmp) / "toc.json"
        proc = subprocess.run(
            [sys.executable, str(script), str(clean), "--pages", "1", "--mode", "text",
             "--out", str(toc_json)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not toc_json.is_file():
            out.append(finding("error", "toc_extract --out 未生成 toc.json"))
        else:
            proc = subprocess.run(
                [sys.executable, str(root / "scripts/chapter_diff.py"),
                 str(toc_json), str(root / "evals/fixtures/chapters-new.md")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0 or "新增" not in proc.stdout:
                out.append(finding("error", "chapter_diff 不能读取 toc.json"))

        messy = Path(tmp) / "messy.pdf"
        doc = fitz.open()
        page = doc.new_page()
        y = 72
        for line in ("第", "预备知识", "S0.1", "集合与映射", "1.22"):
            page.insert_text((72, y), line, fontname="china-s", fontsize=12)
            y += 20
        doc.save(messy)
        doc.close()
        proc = subprocess.run(
            [sys.executable, str(script), str(messy), "--pages", "1", "--mode", "text", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 1 or "重建" not in proc.stderr:
            out.append(finding("error", "toc_extract 对错位目录未退出 1 并提示用 raw 重建"))

        proc = subprocess.run(
            [sys.executable, str(script), str(Path(tmp) / "nope.pdf"), "--pages", "1"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"toc_extract 缺文件应退出 2，实际 {proc.returncode}"))
        proc = subprocess.run(
            [sys.executable, str(script), str(clean), "--pages", "9"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"toc_extract 页码越界应退出 2，实际 {proc.returncode}"))
    return out


def check_page_regions(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/page_regions.py"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            from fitz_compat import fitz
        except ImportError:
            return [finding("info", "跳过 page_regions：未安装 PyMuPDF")]
        base = Path(tmp)
        pdf = base / "two.pdf"
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        page.insert_text((60, 100), "Q1 LEFT alpha only", fontsize=12)
        page.insert_text((340, 100), "Q2 RIGHT beta only", fontsize=12)
        page.insert_text((60, 300), "Q3 LEFT gamma only", fontsize=12)
        page2 = doc.new_page(width=600, height=800)
        page2.insert_text((60, 100), "Q3 continued delta only", fontsize=12)
        page2.insert_text((60, 400), "Q4 LEFT epsilon only", fontsize=12)
        doc.save(pdf)
        doc.close()

        template = base / "template.json"
        proc = subprocess.run(
            [sys.executable, str(script), str(pdf), "--template", "--pages", "1-2",
             "--render", str(base / "render"), "--out", str(template)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not template.is_file():
            out.append(finding("error", f"page_regions 模板失败：{proc.stderr.strip()}"))
        else:
            data = json.loads(template.read_text(encoding="utf-8"))
            if len(data.get("pages") or []) != 2:
                out.append(finding("error", "page_regions 模板页数不为 2"))
            elif not all(Path(row["image"]).is_file() for row in data["pages"]):
                out.append(finding("error", "page_regions 模板未渲染页面图"))
            elif len(data.get("page_size") or {}) != 2:
                out.append(finding("error", "page_regions 模板缺 page_size"))

        numbered = base / "numbered.pdf"
        doc = fitz.open()
        page = doc.new_page(width=600, height=800)
        page.insert_text((60, 100), "27. LEFT alpha only", fontsize=12)
        page.insert_text((340, 100), "29. RIGHT beta only", fontsize=12)
        page.insert_text((60, 300), "28. LEFT gamma only", fontsize=12)
        page.insert_text((340, 300), "30. RIGHT delta only", fontsize=12)
        page2 = doc.new_page(width=600, height=800)
        page2.insert_text((340, 100), "continued epsilon only", fontsize=12)
        page2.insert_text((340, 300), "31. RIGHT zeta only", fontsize=12)
        doc.save(numbered)
        doc.close()

        suggested = base / "suggested.json"
        proc = subprocess.run(
            [sys.executable, str(script), str(numbered), "--suggest", "--pages", "1-2",
             "--out", str(suggested)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not suggested.is_file():
            out.append(finding("error", f"page_regions --suggest 失败：{proc.stderr.strip()}"))
        else:
            data = json.loads(suggested.read_text(encoding="utf-8"))
            rows = data.get("regions") or []
            numbers = [row.get("no") for row in rows]
            if numbers != ["27", "28", "29", "30", "31"]:
                out.append(finding("error", f"--suggest 题号/分栏顺序不对：{numbers}"))
            else:
                if "beta" in rows[0].get("note", "") or "alpha" not in rows[0].get("note", ""):
                    out.append(finding("error", "suggest 左栏串入右栏"))
                if "beta" not in rows[2].get("note", ""):
                    out.append(finding("error", "suggest 右栏题号未识别"))
                if rows[3].get("page_end") != 2 or not rows[3].get("bbox_end"):
                    out.append(finding("error", "suggest 未标出跨页题"))
            crop_rows = base / "suggested-rows.jsonl"
            proc = subprocess.run(
                [sys.executable, str(script), str(numbered), "--regions", str(suggested),
                 "--fig", str(base / "fig"), "--out", str(crop_rows)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0 or not crop_rows.is_file():
                out.append(finding("error", f"--suggest 结果不能直接裁图：{proc.stderr.strip()}"))
            elif len(crop_rows.read_text(encoding="utf-8").splitlines()) != 5:
                out.append(finding("error", "建议 regions 裁图行数不为 5"))

        proc = subprocess.run(
            [sys.executable, str(script), str(pdf), "--suggest", "--pages", "1"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 1:
            out.append(finding("error", f"无题号时 --suggest 应退出 1，实际 {proc.returncode}"))

        regions = base / "regions.json"
        regions.write_text(json.dumps({
            "schema": 1, "paper": "SYN", "unit": "norm",
            "regions": [
                {"no": "1", "page": 1, "bbox": [0.08, 0.10, 0.55, 0.15]},
                {"no": "2", "page": 1, "bbox": [0.55, 0.10, 0.95, 0.15]},
                {"no": "3", "page": 1, "bbox": [0.08, 0.35, 0.55, 0.40],
                 "page_end": 2, "bbox_end": [0.08, 0.10, 0.55, 0.15]},
                {"no": "4", "page": 2, "bbox": [0.08, 0.48, 0.55, 0.53]},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        out_jsonl = base / "rows.jsonl"
        proc = subprocess.run(
            [sys.executable, str(script), str(pdf), "--regions", str(regions),
             "--fig", str(base / "fig"), "--out", str(out_jsonl)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not out_jsonl.is_file():
            out.append(finding("error", f"page_regions 切区失败：{proc.stderr.strip()}"))
        else:
            rows = [json.loads(line) for line in out_jsonl.read_text(encoding="utf-8").splitlines() if line]
            if len(rows) != 4:
                out.append(finding("error", f"page_regions 应输出 4 行，实际 {len(rows)}"))
            else:
                if "alpha" not in rows[0]["stem"] or "beta" in rows[0]["stem"]:
                    out.append(finding("error", f"多栏左栏串入右栏：{rows[0]['stem']}"))
                if "beta" not in rows[1]["stem"] or "alpha" in rows[1]["stem"]:
                    out.append(finding("error", f"多栏右栏串入左栏：{rows[1]['stem']}"))
                if rows[2]["page_start"] != 1 or rows[2]["page_end"] != 2:
                    out.append(finding("error", "跨页题页码不对"))
                if len(rows[2]["images"]) != 2 or "delta" not in rows[2]["stem"]:
                    out.append(finding("error", "跨页题未合并两页裁图/文本"))
                missing = [img for row in rows for img in row["images"] if not Path(img).is_file()]
                if missing:
                    out.append(finding("error", f"裁图未落盘：{missing}"))
            if "regions=4" not in proc.stdout:
                out.append(finding("error", f"page_regions 汇总不对：{proc.stdout.strip()}"))

        px = base / "px.json"
        px.write_text(json.dumps({
            "schema": 1, "unit": "px",
            "page_size": {"1": [1667, 2223]},
            "regions": [{"no": "1", "page": 1, "bbox": [160, 270, 800, 340]}],
        }), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(script), str(pdf), "--regions", str(px),
             "--fig", str(base / "fig"), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or "alpha" not in proc.stdout:
            out.append(finding("error", f"page_regions px 坐标换算失败：{proc.stdout[:200]}"))

        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401
            has_ocr = True
        except ImportError:
            has_ocr = False
        if has_ocr:
            photo = base / "page1.png"
            doc = fitz.open(pdf)
            doc.load_page(0).get_pixmap(dpi=200).save(photo)
            doc.close()
            photo_regions = base / "photo.json"
            photo_regions.write_text(json.dumps({
                "schema": 1, "paper": "PHOTO", "unit": "norm",
                "regions": [{"no": "1", "page": 1, "bbox": [0.08, 0.10, 0.55, 0.15]}],
            }), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script), str(photo), "--regions", str(photo_regions),
                 "--fig", str(base / "fig"), "--ocr", "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0 or "alpha" not in proc.stdout:
                out.append(finding("error", f"page_regions 图片裁区/OCR 失败：{proc.stdout[:200]}"))

        for name, payload in (
            ("badbbox", {"regions": [{"no": "1", "page": 1, "bbox": [0.6, 0.1, 0.4, 0.2]}]}),
            ("dup", {"regions": [{"no": "1", "page": 1, "bbox": [0.1, 0.1, 0.3, 0.2]},
                                 {"no": "1", "page": 1, "bbox": [0.4, 0.1, 0.6, 0.2]}]}),
            ("oob", {"regions": [{"no": "1", "page": 9, "bbox": [0.1, 0.1, 0.3, 0.2]}]}),
        ):
            path = base / f"{name}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(script), str(pdf), "--regions", str(path),
                 "--fig", str(base / "fig")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 2:
                out.append(finding("error", f"page_regions {name} 应退出 2，实际 {proc.returncode}"))
        proc = subprocess.run(
            [sys.executable, str(script), str(base / "nope.pdf"), "--regions", str(regions)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"page_regions 缺文件应退出 2，实际 {proc.returncode}"))
    return out


def check_review_schedule(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/review_schedule.py"
    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        (kb / "records").mkdir(parents=True)
        (kb / "records/cards.jsonl").write_text(
            '{"schema":1,"id":"CARD-0007","ts":"2026-10-01","type":"步骤","title":"t",'
            '"signal":["s"],"method":"m","steps":["a"],"boundary":"b","evidence":[],"status":"verified"}\n'
            '{"schema":1,"id":"CARD-0008","ts":"2026-10-01","type":"定理","title":"u",'
            '"signal":["s"],"method":"m","steps":["a"],"boundary":"b","evidence":[],"status":"hypothesis"}\n',
            encoding="utf-8",
        )
        reviews = kb / "records/reviews.jsonl"
        reviews.write_text(
            '{"schema":1,"id":"REV-0001","card_id":"CARD-0007","ts":"2026-10-20","rating":3,'
            '"interval_days":7,"due":"2026-10-27"}\n'
            '{"schema":1,"id":"REV-0002","card_id":"CARD-0007","ts":"2026-10-27","rating":4,'
            '"interval_days":16,"due":"2026-11-12"}\n'
            '{"schema":1,"id":"REV-0003","card_id":"CARD-0008","ts":"2026-11-01","rating":2,'
            '"interval_days":3,"due":"2026-11-04"}\n',
            encoding="utf-8",
        )

        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--at", "2026-11-08", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            out.append(finding("error", f"review_schedule 回放失败：{proc.stderr.strip()}"))
        else:
            data = json.loads(proc.stdout)
            if data.get("engine") not in {"fsrs", "ladder"}:
                out.append(finding("error", f"review_schedule engine 异常：{data.get('engine')}"))
            cards = {row["card_id"]: row for row in data.get("cards") or []}
            if len(cards) != 2 or "CARD-0007" not in cards:
                out.append(finding("error", f"review_schedule 卡片数不对：{len(cards)}"))
            elif cards["CARD-0007"]["reviews"] != 2:
                out.append(finding("error", "review_schedule 未回放全部历史"))
            elif cards["CARD-0008"]["overdue_days"] <= 0:
                out.append(finding("error", f"review_schedule 未标逾期：{cards['CARD-0008']}"))

        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--at", "2026-11-08",
             "--engine", "ladder", "--grade", "CARD-0007:3", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            out.append(finding("error", f"review_schedule 阶梯评分失败：{proc.stderr.strip()}"))
        else:
            data = json.loads(proc.stdout)
            grade = (data.get("grades") or [{}])[0]
            if data.get("engine") != "ladder" or grade.get("interval_days") != 35:
                out.append(finding("error", f"review_schedule 阶梯连对应为 35 天：{grade}"))
            if grade.get("due_date") != "2026-12-13":
                out.append(finding("error", f"review_schedule 阶梯到期日不对：{grade.get('due_date')}"))

        try:
            import fsrs  # noqa: F401
            has_fsrs = True
        except ImportError:
            has_fsrs = False
        if has_fsrs:
            proc = subprocess.run(
                [sys.executable, str(script), "--kb", str(kb), "--at", "2026-11-08",
                 "--engine", "fsrs", "--grade", "CARD-0007:1", "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0:
                out.append(finding("error", f"review_schedule FSRS 评分失败：{proc.stderr.strip()}"))
            else:
                data = json.loads(proc.stdout)
                grade = (data.get("grades") or [{}])[0]
                if data.get("engine") != "fsrs" or grade.get("state") != "relearning":
                    out.append(finding("error", f"review_schedule FSRS 评分状态不对：{grade}"))
                if not grade.get("due_date"):
                    out.append(finding("error", "review_schedule FSRS 未给出下次到期"))
        else:
            out.append(finding("info", "跳过 review_schedule 的 FSRS 分支：未安装 fsrs"))

        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--at", "2026-11-08",
             "--engine", "ladder", "--grade", "CARD-0008:3", "--record"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            out.append(finding("error", f"review_schedule --record 失败：{proc.stderr.strip()}"))
        else:
            lines = [json.loads(line) for line in reviews.read_text(encoding="utf-8").splitlines() if line]
            if len(lines) != 4:
                out.append(finding("error", f"--record 未追加记录：{len(lines)} 行"))
            else:
                last = lines[-1]
                if (last.get("id") != "REV-0004" or last.get("due") != "2026-11-15"
                        or last.get("engine") != "ladder"):
                    out.append(finding("error", f"--record 写入字段不对：{last}"))

        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--grade", "CARD-9999:2", "--record"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2 or "CARD-9999" not in proc.stderr:
            out.append(finding("error", "--record 对未知卡片未退出 2"))
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(kb), "--grade", "CARD-0007:5"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"非法 rating 应退出 2，实际 {proc.returncode}"))
        proc = subprocess.run(
            [sys.executable, str(script), "--kb", str(Path(tmp) / "nope")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 2:
            out.append(finding("error", f"缺学习库应退出 2，实际 {proc.returncode}"))
    return out


def check_export_and_backlinks(root: Path) -> list[dict]:
    out: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        kb = Path(tmp) / "kb"
        (kb / "records").mkdir(parents=True)
        (kb / "notes").mkdir()
        (kb / "records/cards.jsonl").write_text(
            '{"schema":1,"id":"CARD-0007","title":"0/0 型极限","signal":["含根号"],'
            '"method":"有理化","steps":["有理化","代值"],"boundary":"x→∞ 改用倒代换",'
            '"evidence":["TP-1"],"status":"verified"}\n'
            '{"schema":1,"id":"CARD-0008","title":"洛必达条件","signal":["0/0"],'
            '"method":"先验条件","steps":["判型"],"boundary":"非未定式不可用",'
            '"evidence":[],"status":"hypothesis"}\n',
            encoding="utf-8",
        )
        (kb / "records/errors.jsonl").write_text(
            '{"schema":1,"id":"ERR-0012","chapter":"第2章","concept":"无穷小比较",'
            '"desc":"把 o(x) 当成 O(x)","consequence":"结论错","fix":"先写定义",'
            '"count":2,"status":"open"}\n'
            '{"schema":1,"id":"ERR-0013","chapter":"第1章","concept":"极限",'
            '"desc":"漏写条件","consequence":"丢分","fix":"补条件",'
            '"count":1,"status":"open"}\n',
            encoding="utf-8",
        )
        (kb / "notes/LEC-003-无穷小比较.md").write_text(
            "# 无穷小比较\n\n见 [[CARD-0007]] 与 [[ERR-0012]]，另见 [[LEC-009]]。\n",
            encoding="utf-8",
        )
        (kb / "views").mkdir(exist_ok=True)
        (kb / "views/易错点汇总.md").write_text("手工改过的视图\n", encoding="utf-8")
        view = subprocess.run(
            [sys.executable, str(root / "scripts/kb_view.py"), "--kb", str(kb)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if view.returncode != 0:
            return [finding("error", f"kb_view 失败：{view.stderr.strip() or view.stdout.strip()}")]
        if "曾被手改" not in view.stderr:
            out.append(finding("warn", "kb_view 覆盖手改视图时未告警"))
        links = kb / "views/双链反查.md"
        if not links.is_file():
            out.append(finding("error", "kb_view 未生成 views/双链反查.md"))
        else:
            body = links.read_text(encoding="utf-8")
            if "[[CARD-0007]]" not in body or "断链" not in body:
                out.append(finding("error", "双链反查未列出卡片引用或断链标记"))
            if "| 断链 |" not in body:
                out.append(finding("warn", "双链反查没有断链行，fixture 预期 [[LEC-009]] 断链"))

        for fmt, marker in (("md", "答案区"), ("html", "答案区")):
            target = kb / "views" / f"book.{fmt}"
            proc = subprocess.run(
                [
                    sys.executable, str(root / "scripts/export_book.py"), "--kb", str(kb),
                    "--format", fmt, "--out", str(target),
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if proc.returncode != 0 or not target.is_file():
                out.append(finding(
                    "error", f"export_book {fmt} 失败：{proc.stderr.strip() or proc.stdout.strip()}"
                ))
            elif marker not in target.read_text(encoding="utf-8"):
                out.append(finding("error", f"export_book {fmt} 输出缺答案区"))

        errors_only = kb / "views/errors-only.md"
        proc = subprocess.run(
            [
                sys.executable, str(root / "scripts/export_book.py"), "--kb", str(kb),
                "--errors-only", "--out", str(errors_only),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not errors_only.is_file():
            out.append(finding("error", f"export_book --errors-only 失败：{proc.stderr.strip()}"))
        else:
            body = errors_only.read_text(encoding="utf-8")
            if "CARD-0007" in body or "第一部分" in body:
                out.append(finding("error", "export_book --errors-only 仍导出了题型卡片"))
            if "ERR-0012" not in body:
                out.append(finding("error", "export_book --errors-only 未导出未闭环错题"))

        maxed = kb / "views/max1.md"
        proc = subprocess.run(
            [
                sys.executable, str(root / "scripts/export_book.py"), "--kb", str(kb),
                "--max", "1", "--out", str(maxed),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not maxed.is_file():
            out.append(finding("error", f"export_book --max 失败：{proc.stderr.strip()}"))
        else:
            body = maxed.read_text(encoding="utf-8")
            if "CARD-0007" not in body or "CARD-0008" in body:
                out.append(finding("error", "export_book --max 未把卡片限制到 1 条"))
            if "ERR-0012" not in body or "ERR-0013" in body:
                out.append(finding("error", "export_book --max 未把错题限制到 1 条"))
    return out


def check_confusion_stats(root: Path) -> list[dict]:
    out: list[dict] = []
    script = root / "scripts/kb_stats.py"
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "confusions.jsonl"
        source.write_text(
            '{"schema":1,"id":"CONF-0001","date":"2026-11-01","tag":"无穷小的阶",'
            '"chapter":"第2章","kind":"概念","note":"分不清 o 与 O","ask_count":1}\n'
            '{"schema":1,"id":"CONF-0002","date":"2026-11-03","tag":"无穷小的阶",'
            '"chapter":"第2章","kind":"概念","note":"又问了","ask_count":2}\n'
            '{"schema":1,"id":"CONF-0003","date":"2026-11-04","tag":"换元换限",'
            '"chapter":"第4章","kind":"运算","note":"忘换限","ask_count":1}\n'
            "not json\n",
            encoding="utf-8",
        )
        target = Path(tmp) / "nested" / "stats.md"
        proc = subprocess.run(
            [sys.executable, str(script), str(source), "--output", str(target), "--top", "5"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0 or not target.is_file():
            out.append(finding(
                "error",
                f"kb_stats 失败：{proc.stderr.strip() or proc.stdout.strip()}",
            ))
            return out
        body = target.read_text(encoding="utf-8")
        if "无穷小的阶" not in body or "换元换限" not in body:
            out.append(finding("error", "kb_stats 输出缺困惑点标签"))
        if "专项补讲" not in body:
            out.append(finding("error", "kb_stats 未把累计追问 ≥2 的标签标为专项补讲"))
        if "skipped 1" not in proc.stdout:
            out.append(finding("error", f"kb_stats 未报告坏行计数：{proc.stdout.strip()}"))
    return out


CHECKS = {
    "layout": lambda root, kb: check_layout(root),
    "frontmatter": lambda root, kb: check_frontmatter(root),
    "routing": lambda root, kb: check_routing(root),
    "routing_eval": lambda root, kb: check_routing_eval(root),
    "evals": lambda root, kb: check_evals(root),
    "compile": lambda root, kb: check_compile(root),
    "fitz_compat": lambda root, kb: check_fitz_compat(root),
    "index_roundtrip": lambda root, kb: check_index_roundtrip(root),
    "retrieval_eval": lambda root, kb: check_retrieval_eval(root),
    "intake_gate": lambda root, kb: check_intake_gate(root),
    "pdf_probe": lambda root, kb: check_pdf_probe(root),
    "ocr_page": lambda root, kb: check_ocr_page(root),
    "paper_report": lambda root, kb: check_paper_report(root),
    "weekly_report": lambda root, kb: check_weekly_report(root),
    "slice_questions": lambda root, kb: check_slice_questions(root),
    "chapter_diff": lambda root, kb: check_chapter_diff(root),
    "toc_extract": lambda root, kb: check_toc_extract(root),
    "mock_paper": lambda root, kb: check_mock_paper(root),
    "page_regions": lambda root, kb: check_page_regions(root),
    "review_schedule": lambda root, kb: check_review_schedule(root),
    "step_check": lambda root, kb: check_step_check(root),
    "explain_lint": lambda root, kb: check_explain_lint(root),
    "solution_research": lambda root, kb: check_solution_research(root),
    "web_search": lambda root, kb: check_web_search(root),
    "obsidian_cards": lambda root, kb: check_obsidian_cards(root),
    "anki_export": lambda root, kb: check_anki_export(root),
    "session_digest": lambda root, kb: check_session_digest(root),
    "structure_map": lambda root, kb: check_structure_map(root),
    "cheatsheet": lambda root, kb: check_cheatsheet(root),
    "privacy": lambda root, kb: check_privacy(root),
    "render_rules": lambda root, kb: check_render_rules(root),
    "export_and_backlinks": lambda root, kb: check_export_and_backlinks(root),
    "confusion_stats": lambda root, kb: check_confusion_stats(root),
    "capabilities": lambda root, kb: check_capabilities(root),
    "kb_health": check_kb_health,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", default=".", help="skill 根目录")
    parser.add_argument("--kb", help="要体检的学习库目录")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    root = Path(args.skill_root).resolve()
    kb = Path(args.kb).resolve() if args.kb else None
    manifest = json.loads((root / "checks.json").read_text(encoding="utf-8"))["checks"]
    declared = [c["id"] for c in manifest]

    results: list[dict] = []
    for item in manifest:
        cid = item["id"]
        handler = CHECKS.get(cid)
        if handler is None:
            results.append({"id": cid, "status": "FAIL", "findings": [finding("error", "checks.json 声明的检查没有实现")]})
            continue
        try:
            findings = handler(root, kb)
        except Exception as exc:  # 单条检查崩溃不应吞掉其余检查与 JSON 输出
            findings = [finding("error", f"检查崩溃：{type(exc).__name__}: {exc}")]
        failed = any(f["level"] == "error" for f in findings)
        skipped = any(f["level"] == "info" and "跳过" in f["msg"] for f in findings)
        results.append({
            "id": cid,
            "status": "SKIP" if skipped else ("FAIL" if failed else "PASS"),
            "findings": findings,
        })

    for cid in CHECKS:
        if cid not in declared:
            results.append({"id": cid, "status": "FAIL",
                            "findings": [finding("error", "实现存在但 checks.json 未声明")]})

    if args.json:
        print(json.dumps({"root": str(root), "kb": str(kb) if kb else None, "results": results},
                         ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(f"[{item['status']}] {item['id']}")
            for f in item["findings"]:
                mark = {"error": "!", "warn": "~", "info": "i"}[f["level"]]
                print(f"    {mark} {f['msg']}")
        passed = sum(1 for r in results if r["status"] == "PASS")
        skipped = sum(1 for r in results if r["status"] == "SKIP")
        failed = sum(1 for r in results if r["status"] == "FAIL")
        print(f"\npassed={passed} failed={failed} skipped={skipped}")

    return 1 if any(r["status"] == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
