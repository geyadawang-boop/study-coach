# 知识库维护与存储

存储分三层，各管一件事：

| 层 | 位置 | 角色 | 谁写 |
| --- | --- | --- | --- |
| 叙述层 | `notes/*.md` | 讲义、真题、复盘正文，人类可读 | 模型写，用户改 |
| 记录层 | `records/*.jsonl` | 卡片、错题、困惑、复习、事件，机器可读 | 追加写 |
| 视图层 | `views/*.md` | 由记录层生成的人读汇总 | 脚本生成，不手改 |
| 缓存层 | `.index/index.db` | FTS5 检索索引，可删可重建 | `kb_index.py` |

**两条铁律**：`views/` 不手改；`.index/` 丢了不影响任何数据。

## 目录

```text
学习库/
├── 00-范围与规则.md
├── 01-知识地图.md
├── 02-资料清单.md          ← 由 records/sources.jsonl 渲染
├── notes/
│   ├── LEC-001-函数与极限.md
│   ├── TP-2024A-Q3.md
│   └── PAPER-2024A.md        ← 整卷解剖报告
├── fig/                      ← 题目图、函数图，按卷别-页-题命名
├── records/
│   ├── cards.jsonl
│   ├── errors.jsonl
│   ├── confusions.jsonl
│   ├── reviews.jsonl
│   ├── events.jsonl
│   ├── corrections.jsonl    ← 用户纠错与 AI 易错点日志
│   └── sources.jsonl       ← 资料四标：layer/kind/trust/coverage
├── views/
│   ├── 题型卡片.md
│   ├── 易错点汇总.md
│   ├── 学情快照.md
│   ├── 双链反查.md
│   ├── 高频困惑点统计.md
│   └── 习题册.md / 习题册.html   ← 按需导出，可打印
├── .index/index.db
└── progress.md
```

`sources.jsonl` 的字段与判定规则见 `references/source-layers.md`。`02-资料清单.md` 是视图，不手改。

`progress.md` 只存指针，不存统计：

```markdown
- 当前阶段：第3章讲义
- 最近一讲：LEC-003（退出票：通过）
- 下一入口：LEC-004 入场诊断
- 未闭环缺口：2
- 最近事件：EVT-000123
```

## ID 规则

`CARD-0007` / `ERR-0012` / `CONF-0031` / `REV-0004` / `CORR-0002` / `EVT-000123`：四位零填充（事件六位），只增不改，永不复用。更正用新记录 + `"supersedes": "CARD-0007"`，不原地改历史。

## cards.jsonl

```json
{"schema":1,"id":"CARD-0007","ts":"2026-11-07","type":"步骤","title":"0/0 型极限含根式","signal":["分子分母含根号","x→0"],"method":"有理化 + 等价无穷小","steps":["有理化","整理为乘积","等价替换"],"boundary":"根号内为负或 x→∞ 时改用倒代换","evidence":["TP-2024A-Q3"],"errors":["ERR-0012"],"parent":null,"status":"hypothesis"}
```

规则：

- **卡片分四类，粒度不同**：`定义` 装概念与记号，一张一个概念；`定理` 装条件与结论，必须带适用条件；`步骤` 装一个可执行流程，按步拆；`陷阱` 装一个反例或易错模式，只写“什么信号下会错、怎么避开”。`type` 字段取这四类之一。
- 一张卡只装一个题型；`signal` 必须是题面可见特征，不写“感觉像”。
- `boundary` 必须写反例条件；没有反例说明还没想清楚。
- 新卡先 `hypothesis`；至少 2 道不同来源的题验证后才改 `verified`。
- 一张卡含多个独立步骤时拆成多张，用 `parent` 指向母卡，不把"第几步"塞进一张卡。
- 多步卡的复习评分以最弱一步为准；只评价"整体会了"不算通过。

## 前置依赖与断层检测

知识点不是平铺的。`01-知识地图.md` 每个节点除状态外，还要写 `prerequisites`：

```markdown
| 知识点 | 考纲要求 | 前置 | 状态 | 证据 |
| --- | --- | --- | --- | --- |
| 洛必达法则 | 掌握 | 极限四则运算；0/0 与 ∞/∞ 判型 | L2 | EVT-000031 |
| 泰勒公式 | 掌握 | 高阶导数；等价无穷小 | 未讲 | - |
```

- 前置只写直接依赖，不写整条链；形成的是有向无环图，不允许成环。
- 讲一个新节点前先检查前置状态：前置为 L0/L1 时先补前置，不硬推新内容。
- 做题暴露错误时向上游查一层：错在洛必达，先看判型与四则运算是否 L2。
- 断层写进 `progress.md` 的“未闭环缺口”，并在知识地图标“待补前置”。

## 掌握度：四级 + 证据计数

对外只报四级，内部只数证据，不估概率：

| 级 | 定义 | 需要证据 |
| --- | --- | --- |
| L0 未掌握 | 不能独立开始 | 无 |
| L1 依赖提示 | 在提示 2–3 级下完成 | 1 次带提示正确 |
| L2 独立正确 | 无提示、限时内做对同型题 | 退出票通过 + 1 道变式 |
| L3 稳定迁移 | 换问法、跨章综合仍对，且隔 7 天仍对 | 2 个不同来源的题 + 到期复习 rating ≥ 3 |

规则：

- 升级必须有上表证据，不凭"讲过了"或"听懂了"。
- 连续 2 次错，或到期复习 rating ≤ 2，降一级。
- 单个知识点证据少于 5 次时只报"证据不足"，不报百分比。
- 掌握度写在 `01-知识地图.md` 的状态列，证据 ID 跟在后面，例如 `L2 (EVT-000031, CARD-0007)`。

## corrections.jsonl

```json
{"schema":1,"id":"CORR-0002","ts":"2026-11-07","ref":"LEC-003","claim":"把 o(x) 写成 O(x)","correction":"应为 o(x)，并补一句定义","scope":["note","card"],"ai_pattern":"无穷小阶的记号混用","status":"fixed"}
```

- `ref` 指向被纠正的 note 或 card；`scope` 写受影响的层面。
- `ai_pattern` 用一句话写"我错在哪类模式"，下次讲同知识点先自检。
- 纠错不改历史：note 里标"已更正（CORR-0002）"，卡片新建一条并 `supersedes`。
- 处理完改 `fixed`，未处理保持 `open`。

## errors.jsonl

```json
{"schema":1,"id":"ERR-0012","ts":"2026-11-07","chapter":"第2章","concept":"无穷小比较","desc":"把 o(x) 当成 O(x)","consequence":"结论错","fix":"先写定义再比较阶","count":2,"first_seen":"2026-11-06","source":"LEC-003","status":"open"}
```

- 只收真实发生过的错误；同一错误再次出现时 `count += 1`，不新建。
- `consequence` 只写“丢分 / 结论错 / 表述不规范”三类。
- 已通过变式与回测的改 `closed`，不删除。

## confusions.jsonl

```json
{"schema":1,"id":"CONF-0031","date":"2026-11-07","tag":"无穷小的阶","chapter":"第2章","kind":"概念","note":"分不清 o(x) 与 O(x)","ask_count":1}
```

- `tag` 是复用键，同一概念必须同一写法。
- `kind` 限定：概念 / 运算 / 表述 / 方法选择 / 审题。

## reviews.jsonl

```json
{"schema":1,"id":"REV-0004","card_id":"CARD-0007","ts":"2026-11-07","rating":3,"step_scores":[3,2,3],"interval_days":7,"due":"2026-11-14"}
```

多步卡先逐步问，把最弱一步记进 `rating`；`step_scores` 可选，记录每步评分，`rating` 必须等于其中的最小值。

默认用 FSRS 回放历史算下一次间隔，`interval_days` 是距上次复习的天数（当天学习步小于 1 天，如 0.01）：

```text
# 看所有卡的到期与逾期
python scripts/review_schedule.py --kb 学习库

# 问“下次评 3 分会排到哪天”；--record 追加一条 REV 并写回 due
python scripts/review_schedule.py --kb 学习库 --grade CARD-0007:3 --at 2026-11-08 --record
```

调度器每次从 `records/reviews.jsonl` 按 `ts` 顺序重建状态，不另存黑箱；`--engine ladder` 或未装 `fsrs` 时退回下表，字段完全一致。`--record` 写回时 `interval_days` 取整天、`due` 只存日期（视图按天比较）；要精确到分钟看命令输出里的 `due`。旧记录缺 `engine` 按 FSRS 正常回放。

四档阶梯（仅作降级与对照）：

| rating | 含义 | 下次间隔 |
| --- | --- | --- |
| 1 | 不会 | 1 天 |
| 2 | 想起来一半 | 3 天 |
| 3 | 基本会 | 7 天 |
| 4 | 秒答 | 16 天 |

连续两次 rating ≥3 升到 35 天。判分语义保持不变：`rating` 仍是 1–4，多步卡取最弱一步。

## events.jsonl

```json
{"schema":1,"id":"EVT-000123","ts":"2026-11-07T10:20:00+08:00","stage":"lecture","action":"exit_ticket","ref":"LEC-003","result":"pass","payload":{}}
```

`action` 只用固定值：`intake` / `diagnose` / `lecture` / `drill` / `grade` / `exit_ticket` / `mock` / `override`。`intake` 记录资料入库，`override` 记录用户跳步或坚持推进。

## 生成与检索

```text
# 渲染卡片与错题视图，同时生成 views/双链反查.md
python scripts/kb_view.py --kb 学习库

# 复习排期（FSRS 回放；--engine ladder 可对照四档阶梯）
python scripts/review_schedule.py --kb 学习库 --json

# 困惑点排行
python scripts/kb_stats.py 学习库/records/confusions.jsonl \
  --output 学习库/views/高频困惑点统计.md --top 15

# 建索引（Markdown 是内容真相，索引只是缓存）
python scripts/kb_index.py build 学习库 --db 学习库/.index/index.db
python scripts/kb_index.py search 学习库/.index/index.db "洛必达 适用条件" --top 5
python scripts/kb_index.py search 学习库/.index/index.db "洛必达" --mode vector
python scripts/kb_index.py eval 学习库/.index/index.db evals/retrieval.jsonl --top 5

# 按考点导出可打印习题册（题目区 + 答案区）
python scripts/export_book.py --kb 学习库 --concept 极限 --format html
```

检索结果按路径回读原文，不把片段当引用。

## 双链反查

`kb_view.py` 扫描 `notes/` 与根目录 Markdown 中的 `[[目标]]`，生成 `views/双链反查.md`：

- 目标可以是笔记文件名、笔记 ID 前缀（`LEC-003` → `LEC-003-无穷小比较.md`）、`CARD-*`、`ERR-*`、`CONF-*`；
- 解析不到的目标标「断链」，列出来源 `文件:行`，不静默丢弃；
- 断链处理只有两条：改链接名，或新建对应笔记；不改视图层掩盖。

## 学情快照与周报

`views/学情快照.md` 由 `kb_view.py` 生成，只报有记录支撑的数字：

- 卡片：已验证 / 待验证 / 已退役；
- 到期未复习数量与最早到期日；
- 未闭环错题数与重复次数 Top；
- 最近 7 天事件分布与退出票通过率；
- 资料三层数量。

周报在快照基础上只讲三件事：本周新增薄弱点、已闭环项、下周优先做哪 3 件。没有前后测或没有复习记录的节点，不写"已掌握"。

## 检索评估

改切块、换关键词或重建索引后，用固定 query 集对比，不凭"感觉能搜到"。

- 默认 query 集在 skill 的 `evals/retrieval.jsonl`，覆盖 `subjects/calculus/` 的真实中文问法；有自己的笔记后复制到学习库并只增不删；
- 每行 `{"query": "...", "expect": ["LEC-002", "misconceptions.md"]}`，`expect` 命中路径或标题即算命中；
- 跑 `python scripts/kb_index.py eval <db> evals/retrieval.jsonl --top 5 --mode hybrid`，看 hit@5 与 MRR；
- 三种模式都可跑：`hybrid`（默认，RRF 融合）、`bm25`、`vector`（字符 bigram 余弦）；hybrid 低于全中基线时先查切块粒度与别名，不调 BM25 权重；
- 改过切块规则或公式归一后，先删 `.index/` 再 `build`，否则未变文件会沿用旧切块（构建输出里的 skipped 即未变文件数）；
- query 集只增不删，旧题命不中说明切块退化，不是"题目过时"。

## 导出习题册

`export_book.py` 把卡片与未闭环错题排成「题目区 + 答案区」，默认写 `views/习题册.md`：

- `--concept 关键词` 按考点筛卡片；`--errors-only` 只出错题；`--max N` 限制条数；
- `--format html` 输出可打印页面，浏览器「打印 → 另存为 PDF」即可，不引第三方 PDF 库；
- 题目区只给题面特征与要求，答案区给方法、步骤、边界，不把答案混在题干里；
- 卡片为空或关键词无命中时输出空册并提示，不自动放宽筛选凑数。

## schema 迁移

- 新字段只增不删；旧记录缺字段一律按 `null` 处理，不批量重写历史。
- 结构性变更（改字段语义、拆记录类型）先复制 `records/` 到 `records.bak-<日期>/`，再迁移。
- 每条记录保留 `schema` 版本号；迁移脚本必须可重复执行，且跑完 `kb_health.py` 无 error。
- `.index/` 与 `views/` 不参与迁移，删掉重建。

## 维护节奏

- 每讲结束：补 0–1 张卡，追加 1 条 `exit_ticket` 事件，更新 `progress.md`。
- 每次批改：最多 2 条 `errors`，当场重做原题。
- 每次追问：`confusions` 加 1 条或累加 `ask_count`。
- 每完成一卷：重跑 `kb_view.py`，检查卡片是否重复或可合并。
- 每周：`review_schedule.py` 列到期项并做一轮（`--grade ... --record` 写回）；`kb_index.py build` 增量更新；`kb_view.py` 重出学情快照；`kb_health.py` 无 error。
- 每月：清理已闭环错题到 `closed`，不删除；检查 ID 是否有断号；复盘 `corrections.jsonl` 的 `ai_pattern` 是否重复出现。

## 初始化（从零开始）

首次使用时建目录与空文件，不预填内容：

```text
学习库/
├── 00-范围与规则.md      # 由范围校准写入
├── 01-知识地图.md        # 由知识地图写入
├── notes/                 # 空目录
├── fig/                   # 空目录，存题目图与函数图
├── records/
│   ├── cards.jsonl        # 空文件
│   ├── errors.jsonl
│   ├── confusions.jsonl
│   ├── reviews.jsonl
│   ├── events.jsonl
│   ├── corrections.jsonl
│   └── sources.jsonl      # 第一份资料标定时写入第一条
├── views/                 # 空目录，等 kb_view.py 生成
├── .index/                # 空目录，等 kb_index.py 生成
└── progress.md            # 五行指针
```

先写 `00-范围与规则.md`，再写 `progress.md`，然后才开第一讲。没有旧数据就不做迁移，也不预建卡片。

## 外部工具边界

外部工具只做入口或镜像，**本地 `records/` 才是真相**。

| 工具 | 允许做什么 | 不允许做什么 |
| --- | --- | --- |
| `office-plugin`（pdf） | 把真题 PDF 提取成 `notes/TP-*.md`，保留题号、年份、页图 | 不把提取文本直接当题库，题面缺失先标“待核” |
| `office-plugin`（xlsx） | 生成成绩/失分分布到 `views/` | 不用 Excel 维护卡片或错题；表格是派生视图 |
| Obsidian | 把 `notes/` 作为 vault 或 vault 子目录，用双链跳转 | 不在 Obsidian 里手改 `views/`；改了也不算生效 |
| 飞书 lark | 推送 `views/`、`progress.md` 的只读副本，做共享与提醒 | 飞书上的修改必须回写 `records/` 才算生效 |

同步顺序固定：先写本地 `records/` → 跑脚本生成 `views/` → 再推送副本。反过来会分叉。

Obsidian 的双链约定：文件名带 ID（如 `LEC-003-无穷小比较.md`），链卡片用 `[[CARD-0007]]` 这类 ID，不链标题文字，避免改名断链。
