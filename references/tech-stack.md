# 技术栈与依赖（自用版）

选型原则：**能用标准库就不装，装了没用上就是负债**。单一使用者没有统计样本，任何"模型"都要先证明比规则更准。

## 本机实测（2026-11-08）

| 依赖 | 状态 | 用途 | 处置 |
| --- | --- | --- | --- |
| `sqlite3` | 已装（标准库） | FTS5 检索索引 | 直接可用 |
| `fitz`（PyMuPDF） | 已装 1.28.0 | PDF 文本层与页码提取；`scripts/pdf_probe.py` 用它做 A/B 链路判定 | 直接可用，优先于 OCR；统一经 `scripts/fitz_compat.py` 导入，避免 1.28 的 `import fitz` 弃用横幅污染 stdout |
| `PIL` | 已装 | 图片裁切与预处理 | 直接可用 |
| `sympy` | 已装 | 答案符号校验、等价变形检查 | 默认按 Python 语法；`--latex` 需 antlr4（未装），缺失时退出码 3 |
| `numpy` / `pandas` / `openpyxl` | 已装 | 成绩与失分分布统计 | 按需 |
| `networkx` | 已装 | 知识点前置 DAG | 知识点 > 60 时启用 |
| `chromadb` | 已装 | 向量检索 | **默认不启用**，见升级条件 |
| `fsrs` | 已装 6.3.2（模块名 `fsrs`，非 `py-fsrs`） | FSRS 调度；`scripts/review_schedule.py` 回放 `reviews.jsonl` 算下次到期 | 直接可用；缺失时脚本降级四档阶梯 |
| `mineru` | 未装 | 扫描件/复杂排版解析 | 出现整本扫描教材再装 |
| `pix2tex` | 未装 | 纯公式图片转 LaTeX | 优先让多模态模型直接读图 |

未装的两项不预装。缺什么由实际卡点触发，不提前铺。

Windows 提示：脚本读写文件固定 UTF-8；若控制台或管道里的中文乱码，先设 `$env:PYTHONUTF8="1"` 再运行。不要改系统全局编码，避免影响其他程序。

## 分层选型

| 层 | 当前方案 | 为什么 | 何时升级 |
| --- | --- | --- | --- |
| 文档解析 | `fitz` 提文本层 + 页码范围 | 数字版教材自带文本，OCR 会引入错字且更慢 | 整本扫描件、公式密集且无文本层 → MinerU |
| 拍照/手写 | 多模态模型直接读图，返回结构化 JSON | 少一条 OCR 管线，题号与公式一起识别 | 识别率持续低于 80% 且量大 → 本地 Qwen3-VL |
| 公式 | `step_check.py` 默认按 Python 语法验算；模型输出 LaTeX 时先转写，或装 `antlr4-python3-runtime` 后用 `--latex` | 解析失败会报原始错误并退出，不静默错 | pix2tex 仅在纯公式截图批处理时考虑 |
| 索引 | `scripts/kb_index.py`：SQLite FTS5 `tokenize='trigram'` + BM25 | 零依赖、可重建、中文子串可查（BM25 需 ≥3 字；短查询走 vector/hybrid） | 仍不建第二份向量库 |
| 向量检索 | 字符 bigram 余弦（纯 Python，随索引现算） + RRF 融合，默认 `--mode hybrid` | 不引依赖、不建第二份真相；短查询与同义改写由向量兜底 | 需要真正语义时跑 `kb_index.py embed`，接 `STUDY_COACH_EMBED_CMD` 或 `sentence-transformers`；缓存缺失就退回两路 |
| 符号校验 | `scripts/step_check.py`：逐步验等价 + 特殊值反例（SymPy） | 能证伪，不能证真；结论按"未发现矛盾"表述 | 不升级 |
| 复习调度 | `scripts/review_schedule.py`：FSRS v6 回放 `reviews.jsonl`，字段仍是 `rating/interval_days/due` | 成熟算法，状态可从历史重建，不存黑箱 | 无 `fsrs` 时降级四档阶梯（1/3/7/16，连对 35 天） |
| 掌握度 | 证据计数四级（L0–L3），样本 <5 只报"证据不足" | 单用户样本估不出概率；规则可解释、可复核 | 不升级为 BKT/深度追踪 |
| 可视化 | matplotlib 出函数图；Mermaid/TikZ 出结构图 | 静态图够讲清楚 | 需要动画演示才考虑 Manim |
| 知识图谱 | NetworkX 前置 DAG，存 `records/` 派生 JSON | 前置关系是树/DAG，不需要图数据库 | 不升级 |
| 长期记忆 | 三平面存储 + `progress.md` 指针 + FTS5 检索 | 全部是可读文本，可 diff、可迁移 | 不引入 Letta |
| 评分 | 参考答案对比 + YAML rubric + 步骤匹配，单遍完成 | 可复现、可解释；多模型互评不稳定 | 不引入 debate 评分 |

## 明确不引入

| 方案 | 拒绝理由 |
| --- | --- |
| pyBKT / 深度知识追踪 | 单用户样本量撑不起参数估计；冷启动阶段规则+置信度更可靠，误判成本更低 |
| Letta（MemGPT） | 分层记忆的目标已被三平面存储覆盖；引入运行时会把可读文本变成黑箱状态 |
| GraphRAG | 建图与社区摘要成本高，收益在跨文档实体关系；本场景靠人工三层标定 + BM25 已够 |
| 多 agent 分工/互评 | 增加不可复现性；讲解、出题、批改由同一上下文完成，一致性更好 |
| 自写 SM-2 | 已有现成 `fsrs` 库，直接回放历史；降级阶梯只作兜底，不再自造算法 |
| 多学生隔离 / 租户 | 自用场景，已明确不做 |
| K12 课标知识图谱（K12-KGraph 等） | 目标是大学课程与本校考纲，课标图谱不覆盖；范围层用教材目录 + 考纲 |
| 整卷自动切题商业 API | 试卷含用户私有资料，不外传；本地模型定版面 + `page_regions.py` 裁剪已够 |
| MCP 桥（Anki / Obsidian） | 本地 markdown + JSONL 已是真相源，多一层同步会制造第二份状态 |
| RL 路径规划（PLRS/ALPN）、MCTS 出题 | 单用户数据量不足以训练或评估；排期用规则，出题用母题槽位 |

## 外部参考实现（只借设计，不装依赖）

以下来自调研报告，**未逐条核验原文与指标**，只作架构参考，不作为正确性依据：

| 参考 | 可借的点 | 不借的部分 |
| --- | --- | --- |
| DeepTutor（HKUDS） | 难度校准出题闭环、多分辨率记忆 | 多 agent 编排、向量库依赖 |
| CBIT（EMNLP 2025） | 母题骨架 + 槽位替换的同构题生成 | 训练自己的生成模型 |
| MISTAKE / Error as a Lens | 按错因模拟学生错误，用于干扰项设计 | 用生成模型替代真实错题记录 |
| LaTA / STACK | YAML rubric 逐点评分、CAS 确定性验算 | 本地 LLM 评分服务 |
| IntelliCode（EACL 2026 demo） | 分级提示 + 掌握度更新规则 | 多 agent 集中式学习者模型 |
| MalruleLib / MisstepMath | 可执行 misconception + 步骤轨迹，用于错误模拟 | 自建数据集或训练分类器 |
| Correctness is not Faithfulness（arXiv 2412.18004） | 引用正确 ≠ 引用忠实；逐句回指 | 报告所称 57% 未核原文 |
| RAGAS / ARES | 检索评估指标（hit@k、忠实性）的命名与口径 | 引入评估框架与 LLM judge |
| FSRS / py-fsrs | 成熟调度算法与字段设计 | 已接入 `fsrs` v6；不自写算法 |
| MinerU / MathDoc | 扫描件解析、低置信主动拒绝 | 默认识别链路 |

报告内部存在数字冲突（如 DeepTutor 星数 25k 与 47k、资源数 100 与 150–200），均未核，不引用为结论。第二份 zip 的 24 个 CSV 共 4054 行，实为 3897 个唯一 URL（151 组跨分片重复）；`verified` 字段有 11 种写法，1197 行许可证未知，460 行来自今日头条。只按标题与来源挑高信号条目，不整批导入技能。

## 升级触发条件

一次只换一层，换之前记录基线，换之后用同一批题对比：

1. **bigram 向量 → 外部 embedding**：连续 10 次检索中 ≥3 次"该找到但没找到"，且确认不是关键词写错；先查 `evals/retrieval.jsonl` 是否退化，再跑 `embed` 用同一批 query 对比 hit@5。
2. **FSRS 参数复核**：到期卡积压 > 50，或同一张卡在 7 天内反复遗忘；先查 rating 是否虚高、`step_scores` 是否按最弱一步记，再考虑调参。
3. **fitz → MinerU**：解析后公式/表格缺失导致题目不可用，占比 > 20%。
4. **NetworkX 启用**：知识点条目 > 60，或出现"讲第 7 章时反复回头补第 3 章"。

## 数据格式稳定性

换实现不改 `records/` 字段：

- 换 FSRS 仍写 `rating/interval_days/due`，`rating` 语义保持 1–4。
- 换向量检索仍以 `notes/*.md` 为真相，外部向量库只作 `.index/` 之外的另一个可删缓存。
- 换解析器仍输出 `notes/TP-*.md` 与题目 schema，来源页码写在正文，不改记录层。

## 安装（按需，不预装）

```bash
pip install fsrs          # FSRS 调度（模块名 fsrs，不是 py-fsrs）
pip install "mineru[core]" # 需要解析扫描版教材时
pip install pix2tex        # 需要批量公式图片识别时
```

装完先跑 `python scripts/run_checks.py --skill-root .` 确认没有破坏现有链路。
