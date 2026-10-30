# 切片策略与 metadata 字典

## 四类 chunk

| 类型 | 粒度 | 用途 | 典型问法 |
|---|---|---|---|
| `page` | 整页 | 页级定位、`read_page` 兜底、以图搜图 | "第 7 页讲了什么" |
| `section` | 语义段（≤ max_chars） | 主力检索单元 | "违约金上限是多少" |
| `table` | 单张表 | 数值、对比、汇总类问题 | "预付款比例多少" |
| `image` | 单张图 | 图表结论、示意图结构 | "这个趋势说明什么" |

四类并存不是冗余：`page` 保证位置可追溯，`section` 保证检索精度，`table` 保证数值不被切碎，`image` 保证图表语义不丢。

## metadata 字段字典

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `chunk_id` | str | 是 | 完整唯一 ID，如 `pdf-0123-p3-s2`、`pdf-0123-p2-t0` |
| `doc_id` | str | 是 | 由 PDF 内容 SHA-256 生成；同一文件内容内所有 chunk 一致 |
| `doc_title` | str | 是 | 文档标题，取首个标题块或文件名 |
| `source_sha256` | str | 是 | 原始 PDF 的完整内容哈希 |
| `page` | int | 是 | 页码，1 起 |
| `chunk_type` | str | 是 | page / section / table / image |
| `order` | int | 是 | 在文档中的先后序号，用于取前后文 |
| `section_path` | list[str] | 是 | 章节路径，如 `["第三章 违约责任"]` |
| `char_len` | int | 是 | 字符数，用于判断 chunk 是否过长 |
| `bbox` | list[float] | 是 | `[x0,y0,x1,y1]`，前端高亮用 |
| `source_block_ids` | list[str] | 是 | 回指 structure.json 中的块、表格或图片 ID |
| `text` | str | 是 | 纯原文，引用展示用 |
| `context_prefix` | str | 是 | 上下文说明句 |
| `embed_text` | str | 是 | `context_prefix + "\n" + text`，检索用 |
| `quote_anchor` | str | 是 | `page=3&table=p3-t0` 定位串 |
| `table_id` / `image_id` | str | 否 | 表格/图片编号 |
| `caption` | str | 否 | 题注（"表 2-1 付款节点与金额"） |
| `rows` / `cols` | int | 否 | 表格规模 |
| `embedded_text` | str | 否 | 图内文字（图表标题、坐标轴标签、示意图说明） |
| `visual_desc` | str\|null | 否 | 视觉模型生成的图片描述，未回填为 null |
| `needs_visual_desc` | bool | 否 | 是否待补视觉描述 |

## 为什么 text 与 embed_text 分离

- `embed_text` 带上下文前缀，向量化时能吃到"这段在讲什么、属于哪一章"，召回率显著高于裸文本
- `text` 保持纯净，用于最终引用展示——引用里混进"上文在讲……"会污染原文

对应关系：**检索走 embed_text，引用走 text**。混用会导致两个问题——检索召回差，或引用不像原文。

## 上下文前缀模板

```
《{doc_title}》第{page}页｜所在章节：{section_path}｜本片段讲：{self_summary}｜上文在讲：{prev_summary}｜下文将讲：{next_summary}
```

各摘要截断到 60 字符。缺失项直接省略，不要留空占位（"上文在讲："后面什么都没有会误导模型）。

**作用**：解决"只见树木不见森林"。模型检索到片段后，即便片段本身是半句话，也能通过前缀知道它属于哪一章、前后在讲什么。

## 切片参数

| 参数 | 默认 | 调整依据 |
|---|---|---|
| `--max-chars` | 900 | 合同/条款类可降到 500（一条一 chunk）；研报/叙述类可升到 1200 |
| 切分边界 | 块边界/句边界 | 正常句子不切断；单句仍超限时才兜底硬切 |
| 重叠 | 无 | 靠 `context_prefix` 补跨度信息；重叠会增加重复命中 |

章节路径按阅读顺序跨页延续。表格和图片继承其版面位置之前的最近章节，不再固定为空数组。

## 图文主次策略

| 文档形态 | 主索引 | 具体做法 |
|---|---|---|
| 文字为主 + 少量概念图 | 正文 chunk | 图片仅做摘要索引（题注 + 图内文字 + 视觉描述） |
| 概念图为主 + 少量文字 | 页级视觉描述 | 每页生成详细视觉描述作为主索引，文字降为补充元数据 |

判断依据看类型报告中的 `stats.avg_image_coverage`：超过 0.45 且字符数偏低，基本属于第二类。本技能不内置 embedding；后续接向量库时直接使用 `embed_text`，并保留原 metadata。

## 不该进 chunk 的东西

- 页眉页脚（跨页重复的页码、公司名、"内部资料"）——污染检索，降低区分度
- 表格区域内的散落文本块——已由 table chunk 承载，重复进正文会让数值类问题命中两处、答案不一致
- 图内文字——归 image chunk，否则会在正文里形成假段落
- 目录页——除非用户明确要查目录

## 校验

```bash
python scripts/pdf_tools.py stats chunks.jsonl
```

关注四个数：`chunks` 总量（太少说明切片过粗）、`by_type` 分布（table/image 为 0 而文档明明有图表 → 检测失败）、`待补视觉描述`（未回填会导致图表类问题答不出）、`平均 chunk 长度`（超过 1000 说明该调小 `--max-chars`）。
