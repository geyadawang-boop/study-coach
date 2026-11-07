# 试卷导入与结构化

支持 PDF 文本层、扫描件、手机拍照、图片截图四种输入，统一输出到同一份题目清单再进入解剖。

## 先判定，再选链路

不要一上来就 OCR。先抽样探测：

1. 取前 3 页与中间 3 页，抽字符数：`python scripts/pdf_probe.py 试卷.pdf`（内部用 PyMuPDF/fitz；`--json` 便于写事件）。
2. 每页可读字符 > 200 且公式有文本 → **A 文本层链路**。
3. 字符数接近 0、或全是乱码/方框 → **B 扫描链路**。
4. 输入是 jpg/png/heic → **C 拍照链路**。
5. 同一份文件部分页有文本层 → **逐页判定**，不要整本一刀切。

判定结果与每页字符数写进 `records/events.jsonl`，便于复现。

## 四条链路

| 链路 | 输入 | 处理 | 置信度 |
| --- | --- | --- | --- |
| A 文本层 | 电子版 PDF | 直接抽文本与坐标，表格用 table 抽取 | 高 |
| B 扫描件 | 图片型 PDF | 250–300 dpi 渲染 → OCR → 版面分析分栏 | 中 |
| C 拍照 | 手机照片 | 四点透视纠偏 → 去阴影 → 二值化 → OCR | 中低 |
| D 公式/手写 | 任意来源 | 公式用 pix2tex / UniMERNet 转 LaTeX；手写单独识别 | 低 |

规则：

- A 链路不经过 OCR，公式由模型读文本转 LaTeX，标 `conf=high`。
- B/C 链路先做版面分析，按栏切块，再逐块 OCR；跨栏题目靠题号与空白合并。
- D 链路中手写答案一律标 `待核`，**不用于评分**，只用于了解用户写法。
- 拍照链路必须先纠偏再识别，斜拍不纠偏的识别错误率会翻倍。

## 统一题目 schema

```json
{
  "paper": "MID-2025A",
  "no": "3",
  "page_start": 2,
  "page_end": 2,
  "stem": "计算下列极限……",
  "latex": ["\\lim_{x\\to 0}\\frac{\\sqrt{1+x}-1}{x}"],
  "figure": "fig/MID-2025A-p2-3.png",
  "type": "计算",
  "points": 8,
  "source": "scanned",
  "conf": 0.93,
  "flags": ["公式待核"]
}
```

- `conf` < 0.90 必须进 `flags`，并在解剖报告里单列“待核题”。
- 有图形必须裁图存 `fig/`，不靠文字描述代替。
- 跨页题合并为一条，保留 `page_start` / `page_end`。
- 同一题在多份教辅重复出现，`paper` 记原始卷别，其余记进 `aliases`。

`scripts/intake_check.py` 按上面 schema 做质量门：必需 `paper/no/stem/type/points/source/conf/page_start`（缺失即 FAIL），`page_end` 可省（默认等于 `page_start`）但不得小于它，`latex/flags/aliases` 若出现必须是数组、`figure` 必须是路径字符串。题型分布用 `--expect-types 计算:2,证明:1` 核对；Markdown 卷没有 `type` 字段时该项会报错，改用 JSONL 或去掉该参数。

## 每题的解剖字段

切完题后逐题补结构，不要求用户先做一遍：

```json
{
  "concepts": ["0/0 型极限", "等价无穷小"],
  "difficulty": "基础 | 中 | 难",
  "traps": ["减法结构直接等价替换"],
  "solution_skeleton": ["判型", "有理化", "等价替换", "代值"],
  "textbook_ref": "第1章 1.4 节",
  "parent": "TP-2023B#5"
}
```

- `difficulty` 必须给依据：步骤数、是否需要构造、真题出现频率；不写“感觉难”。
- `parent` 记录母题→变式关系；`parent` 指不到的题不算变式，按独立题处理。
- 没有来源（卷别/教材/教辅名）的题不进库，不参与统计。
- `concepts` 与 `textbook_ref` 都要能回指；归不进去的进“未归类”并说明，不硬塞。

## 整卷报告

逐题解剖完成后汇总成一份，写到 `notes/PAPER-<卷别>.md`：

```markdown
## 卷面结构
| 题型 | 题数 | 分值 | 占比 |

## 考点分布
| 考点 | 题号 | 分值合计 | 教材节 |

## 难度曲线
| 题号 | 难度 | 依据 |

## 陷阱清单
| 陷阱 | 题号 | 后果 |

## 高频母题
| 母题 | 出现题号 | 变式方向 |
```

报告里的每个数字都要能回到题号；考点合计对不上总分时先修数据，不带着错统计做结论。

生成模拟卷时直接复制本报告的卷面结构：题型、题量、分值、难度分布按原卷，评分细则按 `error-taxonomy.md` 的 rubric 逐点写；不自创分值结构。`scripts/mock_paper.py` 可先把这份结构固化成组卷蓝图：每个槽位对应一道母题，rubric 按 `solution_skeleton` 折算到每一步，待核题自动剔除。

## 切题规则

按优先级：

1. 题号正则：`^\s*(\d+)\s*[.、]`、`（\d+）`、`一、二、三、`、`第\d+题`。
2. 版面空白与字体突变作为辅助边界。
3. 大题带小题时，以“大题号 + 小题号”两级存储。

切不准时输出“疑似边界”让人确认，**不硬切**。宁可多一条待确认，不要少一道题。

先跑 `python scripts/slice_questions.py <页面文本或 OCR JSON> --paper <卷别> --out draft.jsonl` 生成草稿：小数（如 3.14）不会被误判为题号，小题号进 `sub`，低置信行标 `待核`；草稿补齐 `type/points` 后再过 `intake_check.py`。

### 多栏与跨页

题号可能先出现在右栏，跨页题又会被页眉页脚隔断，纯文本行切分在这里不可靠。改用“先自动提案、模型复核、脚本裁剪”（PDF 与拍照图片都适用）：

1. `python scripts/page_regions.py 试卷.pdf --suggest --pages 1-2 [--ocr] --out fig/regions.suggested.json`：文本层行框优先，扫描页加 `--ocr`（可用 `--dpi 200`）走 OCR 行框；递归 XY-cut 分栏后按题号聚类，公式行被整行识别成乱码时按左侧窄条回填题号，末题自动剔除页脚。找不到题号退出 1，改走第 2 步模板。
2. 复核建议区域：图表题、跨栏题、一页多个题号段可能偏，必要时 `python scripts/page_regions.py 试卷.pdf --template --pages 1-2 --render fig/pages --out fig/regions.json` 渲染页面，模型看 `pages[].image` 手填 `regions[]`。每条给 `no/page/bbox`（`[x0,y0,x1,y1]`，0–1 归一化、左上原点），跨页题加 `page_end` 与 `bbox_end`。拍照图片只有 `page=1`。
3. `python scripts/page_regions.py 试卷.pdf --regions fig/regions.json --fig fig --out fig/questions.regions.jsonl [--ocr]`：脚本校验页码与 bbox，裁出 `fig/P001-Q3.png`（跨页再出 `fig/P002-Q3.png`），并按区域取文本；无文本层或图片输入时加 `--ocr` 对裁图识别。
4. 草稿带 `page_start/page_end/bbox/images/stem/conf/flags`，模型补 `type/points/concepts` 后再过 `intake_check.py`；`conf<0.90` 或空文本保持 `待核`。

bbox 越界、`x0≥x1` 或题号重复直接退出 2；区域重叠、整页区域给告警。不裁错图、不把相邻栏文字混进同一题。

切题错误记进 `records/events.jsonl`（`action=intake`，payload 写卷别、页码、错型）。下次导入同类卷前先翻最近 3 条 intake 记录，避免重复踩同一个版式。

## 质量门

抽完必须回读核对三项：

- 题数是否与卷面一致；
- 各题分值之和是否等于卷面总分；
- 题型分布是否与卷头说明一致。

**总分对不上是最强的错误信号**，必须停下来修，不能带着错数据做解剖。

`conf` < 0.90 的题先单列“待核清单”，向用户索要清晰照片或原页；待核题不进入考点分布与难度曲线，避免污染整卷统计。

## 不做什么

- 不因为 OCR 不确定就改写题面或补条件。
- 不把几何图压成一句文字描述。
- 不在低置信度下自动生成答案或评分。
- 不把整份试卷直接丢给模型“总结考点”而不做逐题结构化——那样得到的考点无法追溯到题号。
