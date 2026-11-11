# 解法检索与多解对比

讲题默认先给教材体系内的通法。用户要「更优解」「一题多解」「搜一下别人怎么解」，或题目本身有两种以上主流方法时，先检索，再并列给出常规解法与替代解法。

检索不是为了堆方法，而是为了回答一个问题：**这道题在考场上怎么写最稳，有没有更快的验算路径。**

## 什么时候检索

| 触发 | 动作 |
| --- | --- |
| 用户明确说「有没有更优解 / 一题多解 / 搜一下 B 站」 | 必须检索；检索不到就声明降级 |
| 题目有两种以上主流方法（如极限的等价无穷小/泰勒/洛必达） | 默认检索 1 轮，选出值得并列的 1–2 种 |
| 用户问「选择题怎么最快」 | 检索技巧解，但必须给失效条件 |
| 普通讲题、用户只要通法 | 不强制外部检索；先给通法，避免拖慢 |

一次讲解最多检索 1 轮、2 次尝试；单次 60 秒超时。超时或失败立即走降级，不对同一请求反复重试。

## 检索工具

Skill 自带零 API Key 检索脚本，不依赖宿主是否开放搜索工具：

```bash
python scripts/web_search.py "tanx-sinx/x^3 一题多解" \
  --include-domains bilibili.com,math.stackexchange.com,arxiv.org \
  --preferred-domains bilibili.com,math.stackexchange.com \
  --fallback-query "limit (tan x - sin x)/x^3 without lhopital" \
  --max-results 5 --min-results 2 --timeout 60 --max-chars 2800
```

- 顺序固定 Brave → Yahoo → Bing；`status=ok` 即停，同一请求不重复发。
- Bing 是服务端渲染页（`li.b_algo` + `p.b_lineclamp`），专门补 Brave 前端懒加载拿不到摘要的情况；`/ck/a?...&u=a1...` 跳转会解回真实地址，解不出就保留原链接。
- 每轮结果先过相关性门：标题+摘要与查询词（英文按词、中文按 bigram，忽略空格与符号）重合不到 1/3 的整条丢弃，`relevanceDropped` 记录条数；全被丢掉按 `no-relevant-results` 继续下一引擎。反爬降级页常返回「带摘要但整页跑题」的结果，这一关避免把噪音当 `ok`。
- `status=degraded`：有结果但弱（少于 `--min-results`、title/snippet 全空，或 `filterRelaxed=true`），可引用但要标注「结果偏少 / 未按域名过滤」。
- 域名白名单把结果全滤空时不再直接报错：保留原结果、降为 `degraded` 并置 `filterRelaxed=true`，由调用方决定是否采用。
- `--fallback-query`：首个查询无结果、或只拿到弱结果（含 `filterRelaxed`、摘要全空）时补发一次（典型是中文题干配英文关键词打 StackExchange）；`queriesTried` 与 `fallbackUsed` 写进 JSON。
- `status=error`：读 `firstFailureReason`，`challenge-detected` / `timeout` / `no-results` / `no-relevant-results` / `budget-exhausted` 原样写进降级说明。
- 输出 JSON：`results[].title/url/snippet`、`providerAttempts`（含 `snippetCount` / `relevanceDropped`）、`filterRelaxed`、`apiKeyUsed=false`、`resultChars`。
- 只发 GET，不登录、不带 key、不落盘；离线自测用 `--fixture-brave` / `--fixture-yahoo` / `--fixture-bing`。
- 传输优先 `curl.exe`（Yahoo 对 urllib 的指纹常回 500），没有 curl 时退回 urllib 并重试一次。

外部检索统一走这个脚本，用 `--include-domains` 把范围锁在 B 站、StackExchange、arXiv，避免搜到不可控页面。

## 检索顺序

| 顺位 | 来源 | 怎么查 | 可信度 |
| --- | --- | --- | --- |
| 1 | 本地学习库 | `kb_index.py search`，再回读笔记与教材页 | 最高，优先 |
| 2 | 教材/真题答案 | 按 `sources.jsonl` 的页码回读 | 高 |
| 3 | 中文视频平台 | B 站搜「题目关键词 + 一题多解 / 巧解 / 秒杀」；可用 `bilibili-source` 类工具取元数据与字幕 | 中，讲法可参考，结论必须验算 |
| 4 | StackExchange | **必须先把题干关键词译成英文**再搜；只取有推导与评论纠错的问题页 | 中高，只作验算与反例 |
| 5 | arXiv / DOI | 只查定义、定理出处与标准证明 | 高但常超纲，标 `[拓展]` |
| 6 | 中文社区/博客 | 知乎、CSDN、公众号 | 低，只作线索 |

**StackExchange 不做首选**：它按英文术语索引，中文题干直搜命中率低；内容偏标准论证，与国内考纲和教材体系也不一致。只有在需要标准推导、反例或英文术语对照时才用它，且结论只用于验算解法是否正确，不能当考纲依据。

B 站优先看有完整板书、步骤可暂停复现的视频；只有口播结论、没有步骤的不收。学术站点优先看有推导与评论纠错的问题页，不只看 accepted answer。

## 查询模板

```text
<题目核心式> 一题多解
<题目核心式> 巧解
<题目核心式> site:bilibili.com
<题目核心式> 秒杀
<英文题干关键词> alternative solution
<英文题干关键词> site:math.stackexchange.com
<定理英文名> proof site:arxiv.org
```

题目 core 只保留可检索的特征，例如「tanx-sinx/x^3 极限」，不要把整段中文题面塞进搜索框。中文题面**不要**直接拼 `site:math.stackexchange.com`，先译成英文关键词，否则等于空转一轮。

## 每条候选解法的准入条件

候选解法必须同时满足：

1. **能复现**：第一步用哪个定理/变形，后续每步有依据，不是只给答案。
2. **能验算**：通过 `scripts/step_check.py` 的逐步等价检查，或用 2–3 个特殊值/数值点验证。
3. **不超纲**：超出考纲的标 `[拓展]`，不进考场推荐。
4. **有边界**：写清什么时候失效；没有失效条件的「秒杀法」不收。
5. **来源可回指**：URL + 访问日期；视频写 BV 号或链接。

只满足「答案一样」的解法不算候选。网上常见但无法验证的说法，最多写「待核」，不列为更优解。

## 输出格式

常规解法必给；替代解法最多并列 2 种，避免变成方法展览。

```markdown
## 常规解法
（步骤 + 每步依据）

## 多解对比
| 解法 | 关键步骤 | 适用 | 考场耗时 | 风险 | 验算 |
| --- | --- | --- | --- | --- | --- |
| 常规：…… | …… | 所有同类题 | 约 N 分钟 | …… | 数值/符号验算 |
| 巧解：…… | …… | 选择题/检验 | 约 N 分钟 | 失效条件 | 数值/符号验算 |

## 验算
（特殊值表或 step_check 结果）

## 来源
- 教材：DOC-0001#p58
- B 站：https://www.bilibili.com/video/BV...（访问 2026-11-08）
- Math StackExchange：https://...（访问 2026-11-08）
- 未外部检索的部分：……（如有）

## 一句话总结
（考场上先写哪种，什么时候换）
```

规则：

- 常规解法永远排在第一位，答题卡默认写常规解法。
- 巧解只用于选择题、填空或检验；用户要写大题时，明确说「巧解可略，通法必须留」。
- 对比表必须有「适用」「风险」「验算」三列，缺一列不算成稿。
- 来源里至少有一个真实链接；完全没联网时写「未外部检索」，并说明替代解法来自本地推导。

## 检索失败与降级

```text
未查到可用的外部解法（web_search 返回 status=error，原因：challenge-detected / timeout / no-results）。
以下给两种本地推导：常规解法 + 一种替代解法，均已验算。
外部来源标 [未外部检索]，需要我换关键词再试吗？
```

不允许：

- 把模型记忆说成「B 站上都说」。
- 列没打开过的链接。
- 搜到一个无法验算的「秒杀法」就推荐。
- 用外部解法否定教材定义；口径冲突时标「口径不同」，按 `source-layers.md` 裁决。

## 质量门

多解稿写完跑：

```bash
python scripts/explain_lint.py note.md --level multi
```

缺常规解法、对比表少于 2 种解法、缺「适用」列、缺验算、缺来源，都会退出 1。
