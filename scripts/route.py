#!/usr/bin/env python3
"""Route a user utterance to the reference or script that handles it.

This is a deterministic aid for the routing table in SKILL.md, not a trained
intent model: it scores character-bigram overlap between the utterance and the
example phrasings declared below, adds a small bonus for domain keywords, and
returns the top-3 entries. `--eval` replays evals/routing.jsonl so coverage is
measured instead of assumed.

Usage:
  python route.py "这卷子是左右两栏，还有一题连到下一页"
  python route.py --eval evals/routing.jsonl
  python route.py --eval evals/routing.jsonl --json --min-top1 0.85 --min-top3 0.95

Exit codes: 0 = routed / thresholds met; 1 = no match or eval below threshold;
2 = bad input.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

INTENTS = [
    {
        "id": "source-layers",
        "entry": "references/source-layers.md",
        "phrases": [
            "第一次使用要准备哪些资料", "教材考纲真题怎么分层", "两份资料说法冲突听谁的",
            "给资料打 layer kind trust coverage 四个标", "资料缺口检测怎么做",
        ],
        "keys": ["资料", "分层", "冲突", "考纲"],
    },
    {
        "id": "lecture-and-drill",
        "entry": "references/lecture-and-drill.md",
        "phrases": [
            "帮我开一讲新内容", "设计自测题和退出票", "讲练测九步流程怎么走",
            "作业模式只给思路不给答案", "提示分五级逐步给", "渐隐练习怎么安排",
            "这题卡住了先别给完整解法", "没有思路时怎么引导",
        ],
        "keys": ["讲", "自测", "提示", "退出票", "思路", "解法", "卡住"],
    },
    {
        "id": "paper-intake",
        "entry": "references/paper-intake.md",
        "phrases": [
            "导入一份试卷并结构化", "把真题卷整理成题目数据", "题目 schema 需要哪些字段",
            "扫描试卷怎么入库", "题目要标考点难点陷阱", "卷子转成 jsonl 题目",
        ],
        "keys": ["试卷", "题目", "schema", "入库"],
    },
    {
        "id": "error-taxonomy",
        "entry": "references/error-taxonomy.md",
        "phrases": [
            "批改错题并归因", "错因分成哪五类", "出同构变式和反事实变式",
            "记录误解库并统计", "错误路径预生成", "做完题帮我看看哪里错了",
            "又错了是不是根本没懂",
        ],
        "keys": ["批改", "归因", "变式", "错因", "错"],
    },
    {
        "id": "knowledge-base",
        "entry": "references/knowledge-base.md",
        "phrases": [
            "卡片字段怎么设计", "records 和 views 目录结构", "掌握度 L0 到 L3 的证据",
            "复习记录怎么追加", "旧记录用 supersede 更正", "笔记存储三平面",
            "掌握程度怎么判定", "掌握度需要哪些证据",
        ],
        "keys": ["字段", "目录", "掌握", "记录"],
    },
    {
        "id": "research",
        "entry": "references/research.md",
        "phrases": [
            "讲之前先检索资料", "引用要能回指页码", "检索不到怎么降级",
            "不要编造资料原文", "引用 URL 和文档编号",
        ],
        "keys": ["检索", "引用", "回指"],
    },
    {
        "id": "output-contract",
        "entry": "references/output-contract.md",
        "phrases": [
            "输出格式要固定成什么样", "公式渲染有什么禁忌", "题型卡片模板",
            "禁止 left right 和 mathbb", "渲染前要检查什么",
        ],
        "keys": ["格式", "渲染", "模板"],
    },
    {
        "id": "tech-stack",
        "entry": "references/tech-stack.md",
        "phrases": [
            "依赖要装哪些库", "什么时候该上 OCR 或换实现", "不引入重依赖的选型",
            "本地优先还是调用外部接口", "安装命令和版本",
        ],
        "keys": ["依赖", "选型", "安装"],
    },
    {
        "id": "pdf-probe",
        "entry": "scripts/pdf_probe.py",
        "phrases": [
            "判断 PDF 是文本层还是扫描件", "先探测 PDF 走 A 链还是 B 链",
            "PDF 有没有文字层怎么查", "混合页面的 PDF 怎么判",
        ],
        "keys": ["探测", "文本层", "扫描件", "PDF"],
    },
    {
        "id": "ocr-page",
        "entry": "scripts/ocr_page.py",
        "phrases": [
            "扫描页识别文字", "拍照单页做 OCR", "把某一页渲染成图片再识别",
            "OCR 置信度低要标待核",
        ],
        "keys": ["OCR", "识别", "拍照"],
    },
    {
        "id": "intake-check",
        "entry": "scripts/intake_check.py",
        "phrases": [
            "切题结果质检", "题数总分和题型分布对不上", "校验 schema 和页码区间",
            "把待核题剔出统计", "质量门不通过就不入库",
        ],
        "keys": ["质检", "校验", "总分", "题数"],
    },
    {
        "id": "slice-questions",
        "entry": "scripts/slice_questions.py",
        "phrases": [
            "从页面文本切出题目草稿", "按题号切题但小数不要误切",
            "切题边界不确定要标出来", "OCR 结果切成题目草稿",
        ],
        "keys": ["切题", "草稿", "边界"],
    },
    {
        "id": "page-regions",
        "entry": "scripts/page_regions.py",
        "phrases": [
            "两栏试卷怎么切题", "题目跨页了怎么裁", "按 bbox 裁图逐区取文",
            "渲染页面给模型定坐标", "多栏排版不能用题号重排猜边界",
        ],
        "keys": ["多栏", "跨页", "bbox", "裁图"],
    },
    {
        "id": "review-schedule",
        "entry": "scripts/review_schedule.py",
        "phrases": [
            "下次复习安排在哪天", "按复习历史算间隔", "FSRS 排期怎么跑",
            "复习评分写回记录", "到期复习清单", "这张卡什么时候再复习",
        ],
        "keys": ["复习", "间隔", "排期", "到期", "再复习"],
    },
    {
        "id": "paper-report",
        "entry": "scripts/paper_report.py",
        "phrases": [
            "整卷分析考点分布和难度曲线", "输出题型分值和陷阱清单",
            "高频母题统计", "整卷报告剔除待核题", "真题卷都考了什么",
            "分值怎么分布",
        ],
        "keys": ["整卷", "考点分布", "难度", "分值"],
    },
    {
        "id": "mock-paper",
        "entry": "scripts/mock_paper.py",
        "phrases": [
            "组一套模拟卷", "按真题结构生成组卷蓝图", "模拟卷逐步 rubric 怎么写",
            "限时模拟卷怎么配分",
        ],
        "keys": ["模拟卷", "组卷", "rubric"],
    },
    {
        "id": "toc-extract",
        "entry": "scripts/toc_extract.py",
        "phrases": [
            "扫描书没有内嵌目录", "从印刷目录页抽章节树",
            "目录识别错位要重建", "给模型校对 OCR 目录",
        ],
        "keys": ["目录", "章节树", "目录页"],
    },
    {
        "id": "chapter-diff",
        "entry": "scripts/chapter_diff.py",
        "phrases": [
            "教材换版后章节怎么对比", "新版删了哪些题", "页码偏移和退役题",
            "新旧教材章节树 diff",
        ],
        "keys": ["换版", "对比", "退役", "旧版"],
    },
    {
        "id": "weekly-report",
        "entry": "scripts/weekly_report.py",
        "phrases": [
            "生成本周学习周报", "统计近七天退出票和错题", "这周复习了多少",
            "周报里列到期复习和困惑",
        ],
        "keys": ["周报", "本周", "一周", "近七天"],
    },
    {
        "id": "kb-view",
        "entry": "scripts/kb_view.py",
        "phrases": [
            "生成题型卡片和错题视图", "双链反查断链", "学情快照视图怎么渲染",
            "手改 views 要告警",
        ],
        "keys": ["视图", "双链", "快照"],
    },
    {
        "id": "kb-stats",
        "entry": "scripts/kb_stats.py",
        "phrases": [
            "统计高频困惑点", "confusions 按出现次数排",
            "最近老在同一个地方错", "困惑点按考点聚合",
        ],
        "keys": ["困惑", "统计", "高频"],
    },
    {
        "id": "kb-index",
        "entry": "scripts/kb_index.py",
        "phrases": [
            "跨讲义和真题检索", "建立全文索引", "按定理步骤例题陷阱过滤",
            "评估检索 hit@k", "给学习库建全文索引",
        ],
        "keys": ["索引", "检索", "全文", "建索引", "陷阱"],
    },
    {
        "id": "export-book",
        "entry": "scripts/export_book.py",
        "phrases": [
            "导出可打印习题册", "按考点导出错题", "习题册题目和答案分开",
        ],
        "keys": ["导出", "习题册", "打印"],
    },
    {
        "id": "step-check",
        "entry": "scripts/step_check.py",
        "phrases": [
            "验算这一步等价吗", "用 sympy 检查解题步骤", "逐步等价和特殊值验算",
        ],
        "keys": ["验算", "等价", "sympy"],
    },
    {
        "id": "solution-research",
        "entry": "references/solution-research.md",
        "phrases": [
            "这题有没有更优解", "一题多解搜一下别人怎么解",
            "去B站找这道题的巧解", "同时给我常规解法和多种解法",
            "这题在StackExchange上怎么解",
        ],
        "keys": ["更优解", "多解", "巧解", "B站", "多种解法"],
    },
    {
        "id": "web-search",
        "entry": "scripts/web_search.py",
        "phrases": [
            "跑一下零key检索脚本", "web_search 怎么指定只搜某几个域名",
            "检索脚本遇到反爬怎么办", "不用 API key 怎么搜网页",
            "web_search 的字符预算怎么设",
        ],
        "keys": ["web_search", "零key", "零 key", "反爬", "字符预算", "域名过滤"],
    },
    {
        "id": "explain-lint",
        "entry": "scripts/explain_lint.py",
        "phrases": [
            "讲题稿写完检查一下结构", "追问稿够不够深", "讲题七段缺了哪段",
            "追问有没有两种视角和数值验证", "成稿前跑一下结构检查",
        ],
        "keys": ["讲题稿", "追问稿", "七段", "结构检查"],
    },
    {
        "id": "kb-health",
        "entry": "scripts/kb_health.py",
        "phrases": [
            "体检学习库有没有坏数据", "检查 records 的 JSONL 是否合法",
            "学习库目录缺文件", "跑一遍 skill 验收",
        ],
        "keys": ["体检", "健康", "坏数据", "验收"],
    },
    {
        "id": "route",
        "entry": "scripts/route.py",
        "phrases": [
            "不知道该读哪份文档", "该跑哪个脚本", "入口不确定先路由",
            "资源路由表怎么用",
        ],
        "keys": ["哪个文档", "哪个脚本", "入口"],
    },
]


def grams(text: str) -> Counter:
    clean = re.sub(r"[^\w]+", "", str(text).lower())
    if len(clean) < 2:
        return Counter([clean]) if clean else Counter()
    return Counter(clean[i:i + 2] for i in range(len(clean) - 1))


def cosine(left: Counter, right: Counter) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    dot = sum(left[g] * right[g] for g in common)
    if not dot:
        return 0.0
    norm = sum(v * v for v in left.values()) ** 0.5
    norm *= sum(v * v for v in right.values()) ** 0.5
    return dot / norm


def rank(query: str) -> list[dict]:
    query_grams = grams(query)
    scored = []
    for intent in INTENTS:
        best = max(cosine(query_grams, grams(phrase)) for phrase in intent["phrases"])
        hits = sum(1 for key in intent["keys"] if key in query)
        scored.append({
            "intent": intent["id"],
            "entry": intent["entry"],
            "score": round(best + min(0.20, 0.06 * hits), 4),
        })
    return sorted(scored, key=lambda row: row["score"], reverse=True)


def eval_cases(path: Path, min_top1: float, min_top3: float, as_json: bool) -> int:
    cases = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"{path.name} 第 {number} 行无法解析：{exc}", file=sys.stderr)
            return 2
        if not str(case.get("input") or "").strip() or not str(case.get("expect") or "").strip():
            print(f"{path.name} 第 {number} 行缺 input/expect", file=sys.stderr)
            return 2
        cases.append(case)
    if not cases:
        print("没有评测用例", file=sys.stderr)
        return 2

    rows = []
    top1 = top3 = 0
    for case in cases:
        ranked = rank(case["input"])
        entries = [row["entry"] for row in ranked[:3]]
        hit1 = entries[0] == case["expect"]
        hit3 = case["expect"] in entries
        top1 += hit1
        top3 += hit3
        rows.append({
            "id": case.get("id") or "",
            "input": case["input"],
            "expect": case["expect"],
            "top1": entries[0],
            "top3": entries,
            "hit1": hit1,
            "hit3": hit3,
        })
    total = len(rows)
    rate1, rate3 = top1 / total, top3 / total
    if as_json:
        print(json.dumps({"total": total, "top1": top1, "top3": top3,
                          "rate1": round(rate1, 4), "rate3": round(rate3, 4),
                          "rows": rows}, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            mark = "ok" if row["hit1"] else ("3" if row["hit3"] else "!!")
            print(f"[{mark}] {row['id']} top1={row['top1']} expect={row['expect']}")
            if not row["hit3"]:
                print(f"     top3={row['top3']}")
        print(f"top1={top1}/{total} ({rate1:.0%}) top3={top3}/{total} ({rate3:.0%})")
    ok = rate1 >= min_top1 and rate3 >= min_top3
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="用户原话")
    parser.add_argument("--eval", help="路由评测 JSONL")
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--min-top1", type=float, default=0.85)
    parser.add_argument("--min-top3", type=float, default=0.95)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    if args.eval:
        path = Path(args.eval)
        if not path.is_file():
            print(f"not a file: {path}", file=sys.stderr)
            return 2
        return eval_cases(path, args.min_top1, args.min_top3, args.json)
    if not args.query:
        print("需要一句用户原话，或用 --eval", file=sys.stderr)
        return 2
    ranked = rank(args.query)
    if args.json:
        print(json.dumps({"query": args.query, "ranked": ranked[:args.top]},
                         ensure_ascii=False, indent=2))
    else:
        for row in ranked[:args.top]:
            print(f"{row['score']:.3f}  {row['intent']:<16} {row['entry']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
