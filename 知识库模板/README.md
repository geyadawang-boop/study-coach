# 本地知识库模板

这是 `study-coach` 附带的最小可用学习库。复制成 `学习库/` 后即可开始，不需要从零建目录。

## 用法

```bash
# 在 skill 根目录执行：复制模板
cp -r 知识库模板 学习库

# 或 Windows PowerShell
Copy-Item -Recurse 知识库模板 学习库

# 体检：应输出 errors=0 warnings=0
python scripts/kb_health.py 学习库

# 生成视图（02-资料清单、题型卡片、易错点汇总、学情快照、双链反查）
python scripts/kb_view.py --kb 学习库

# 生成高频困惑点统计
python scripts/kb_stats.py 学习库/records/confusions.jsonl \
  --output 学习库/views/高频困惑点统计.md

# 建索引（可删可重建）
python scripts/kb_index.py build 学习库 --db 学习库/.index/index.db
python scripts/kb_index.py search 学习库/.index/index.db "有理化" --top 5
```

## 目录说明

| 位置 | 作用 | 谁写 |
| --- | --- | --- |
| `00-范围与规则.md` | 考试信息、范围、已确认/待确认 | 范围校准阶段 |
| `01-知识地图.md` | 知识点、前置、状态、证据 | 知识地图阶段 |
| `02-资料清单.md` | 由 `records/sources.jsonl` 渲染 | `kb_view.py` |
| `notes/` | 讲义、真题、复盘正文 | 模型写，用户改 |
| `records/` | 卡片、错题、困惑、复习、事件、纠错、资料 | 追加为主，状态字段可更新 |
| `views/` | 人读汇总 | 脚本生成，不手改 |
| `.index/` | FTS5 检索缓存 | `kb_index.py`，可删 |
| `fig/` | 题目图、函数图 | 导入/裁图脚本 |
| `progress.md` | 当前阶段与下一入口 | 每讲结束更新 |

## 模板里有哪些占位

- `DOC-0001`：示例教材，替换成你的主教材；四标必须齐全。
- `LEC-001`：示例讲义，替换成第一讲；`CARD-0001`、`ERR-0001`、`CONF-0001` 都指向它。
- `EVT-000001`：示例事件，`progress.md` 的「最近事件」指向它。
- `reviews.jsonl`、`corrections.jsonl`：空文件，等真实复习与纠错发生后再追加。

## 三条铁律

1. `views/` 不手改，改了下次生成会被覆盖。
2. `.index/` 丢了不影响任何数据，删除后重建即可。
3. `records/` 追加为主（`errors.count`、`confusions.ask_count`、`status` 可原地更新）；事实更正用新记录加 `supersedes`，ID 永不复用。

存储字段与掌握度规则见 `references/knowledge-base.md`。
