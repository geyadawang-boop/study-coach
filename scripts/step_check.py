#!/usr/bin/env python3
"""Verify a chain of algebraic steps with SymPy.

Each adjacent pair is checked twice:

1. symbolic: simplify(a - b) == 0
2. numeric: substitute sample values; a non-zero difference is a counterexample

With --limit-at P, a step that is not an identity is accepted only if
lim_{var->P} (a - b) = 0; the verdict is then marked LIMIT, not OK.

This separates "answer happens to be right" from "every step is right" —
the failure mode where a wrong intermediate step still lands on the correct
final value.

Usage:
  python step_check.py --steps "(sqrt(1+x)-1)/x" "1/(sqrt(1+x)+1)" "1/2" \
      --var x --limit-at 0 --expect "1/2"
  python step_check.py --steps "x+x" "2*x"
  python step_check.py --steps "\\frac{x}{2}" "x/2" --latex

Exit code: 0 all OK/LIMIT, 1 any UNKNOWN or unparsable input,
2 any FAIL, 3 sympy or LaTeX-parser dependency missing.
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    import sympy as sp
except ImportError:  # pragma: no cover - environment dependent
    print("需要 sympy：pip install sympy", file=sys.stderr)
    raise SystemExit(3)

SAMPLES = [sp.Rational(1, 3), sp.Rational(7, 5), sp.Rational(-5, 9), sp.Rational(13, 7)]
TOL = 1e-8


def parse_expr(text: str, latex: bool = False):
    if latex:
        from sympy.parsing.latex import parse_latex

        return parse_latex(text)
    return sp.sympify(text, evaluate=True)


def pick_symbol(exprs: list, name: str | None):
    if name:
        return sp.Symbol(name)
    found: set = set()
    for expr in exprs:
        found |= expr.free_symbols
    if not found:
        return None
    if len(found) == 1:
        return found.pop()
    raise SystemExit(f"多个自由符号 {sorted(map(str, found))}，请用 --var 指定")


def _value(expr, var, point):
    try:
        val = complex(sp.N(expr.subs(var, point)))
    except Exception:
        return None
    if val != val or abs(val) == float("inf"):
        return None
    return val


def probe(expr_a, expr_b, diff, var) -> tuple[bool | None, str]:
    """Return (all_zero, evidence). None means no usable sample point."""
    if var is None:
        return None, "无自由符号，跳过数值探测"
    tested = 0
    for point in SAMPLES:
        dv = _value(diff, var, point)
        if dv is None:
            continue
        va = _value(expr_a, var, point)
        vb = _value(expr_b, var, point)
        scale = max(1.0, abs(va or 0.0), abs(vb or 0.0))
        tested += 1
        if abs(dv) > TOL * scale:
            return False, f"{var}={point} 时差值为 {dv:.3g}"
    if tested == 0:
        return None, "样本点均无定义"
    return True, f"{tested} 个样本点差值为 0"


def step_verdict(expr_a, expr_b, var, limit_at: str | None) -> tuple[str, str]:
    diff = sp.simplify(expr_a - expr_b)
    if diff != 0:
        for rewrite in (sp.cancel, sp.trigsimp, sp.expand, sp.radsimp):
            try:
                candidate = rewrite(diff)
            except Exception:
                continue
            if candidate == 0:
                diff = candidate
                break
    if diff == 0:
        return "OK", "符号化简差为 0"

    all_zero, evidence = probe(expr_a, expr_b, diff, var)

    # 极限模式下，相邻步只在极限点相等是正常的，先算极限再判。
    if limit_at is not None and var is not None:
        try:
            point = sp.sympify(limit_at)
            lim = sp.limit(diff, var, point)
        except Exception as exc:
            return "UNKNOWN", f"符号未归零，极限计算失败：{exc}；{evidence}"
        if lim == 0:
            return "LIMIT", f"仅在 {var}→{point} 时差极限为 0（非恒等）；{evidence}"
        return "FAIL", f"{var}→{point} 时差极限为 {lim}"

    if all_zero is False:
        return "FAIL", f"数值反例：{evidence}"
    return "UNKNOWN", f"符号未化简为 0；{evidence}"


def verify(
    steps: list[str],
    var_name: str | None = None,
    limit_at: str | None = None,
    expect: str | None = None,
    latex: bool = False,
) -> dict:
    exprs = [parse_expr(item, latex) for item in steps]
    var = pick_symbol(exprs, var_name)
    results = []
    for index in range(len(exprs) - 1):
        verdict, evidence = step_verdict(exprs[index], exprs[index + 1], var, limit_at)
        results.append(
            {"pair": f"{index + 1}->{index + 2}", "verdict": verdict, "evidence": evidence}
        )

    if expect is not None and exprs:
        verdict, evidence = step_verdict(exprs[-1], parse_expr(expect, latex), var, limit_at)
        results.append({"pair": "final=expect", "verdict": verdict, "evidence": evidence})

    if any(item["verdict"] == "FAIL" for item in results):
        summary = "FAIL"
    elif any(item["verdict"] == "UNKNOWN" for item in results):
        summary = "UNKNOWN"
    else:
        summary = "OK"
    return {"summary": summary, "var": str(var) if var is not None else None, "steps": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--steps", nargs="+", required=True, help="按顺序给出每一步的表达式")
    parser.add_argument("--var", help="自变量名，默认从表达式中推断")
    parser.add_argument("--limit-at", help="非恒等步骤允许的极限点，例如 0 或 oo")
    parser.add_argument("--expect", help="最终答案，给出时额外核对最后一步")
    parser.add_argument("--latex", action="store_true",
                        help="按 LaTeX 解析输入（需要 antlr4-python3-runtime）")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    try:
        report = verify(args.steps, args.var, args.limit_at, args.expect, args.latex)
    except ImportError as exc:
        print(
            f"LaTeX 解析需要 antlr4-python3-runtime：pip install antlr4-python3-runtime；"
            f"或去掉 --latex 改用 Python 语法。原始错误：{exc}",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:  # 输入语法错误：保留原始错误，不静默吞掉
        print(f"无法解析或验算输入（{type(exc).__name__}）：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["steps"]:
            print(f"[{item['verdict']}] {item['pair']}  {item['evidence']}")
        print(f"summary={report['summary']}")

    if report["summary"] == "FAIL":
        return 2
    if report["summary"] == "UNKNOWN":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
