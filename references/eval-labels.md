# PDF RAG 评测标签

评测集用 JSONL，每行一个问题。先生成与当前索引对应的模板：

```bash
python scripts/eval.py --template-out labels.template.jsonl chunks.jsonl
```

模板默认抽取 30 个 section/table chunk。必须把空问题替换成真实业务问题，并核对 gold chunk 和页码。

## 字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `question` | 是 | 用户真实问法，不要把答案写进问题 |
| `gold_chunk_ids` | 是 | 能独立支持正确答案的 chunk ID 列表 |
| `gold_pages` | 是 | 正确 PDF 页序号列表 |
| `answer_type` | 是 | `fact`、`table`、`chart`、`cross_section`、`aggregate` 等 |
| `expected_values` | 否 | 表格题必须匹配的原始数值，如 `["30%", "3,840,000"]` |
| `notes` | 否 | 标注说明 |

示例：

```json
{"question":"预付款比例是多少？","gold_chunk_ids":["pdf-0123-p2-t0"],"gold_pages":[2],"answer_type":"table","expected_values":["30%"]}
{"question":"设备逾期交付的违约金上限是多少？","gold_chunk_ids":["pdf-0123-p3-s1"],"gold_pages":[3],"answer_type":"fact"}
```

## 指标

```bash
python scripts/eval.py labels.jsonl chunks.jsonl --k 5
```

- `recall@k`：gold chunk 出现在前 k 个结果中的比例。
- `mrr`：第一个 gold chunk 排名倒数的平均值。
- `page_accuracy`：Top-1 chunk 的 PDF 页序号是否命中 gold page。
- `table_value_match`：带 `expected_values` 的问题，其数值是否出现在前 k 个证据中。
- `type_breakdown`：按 `answer_type` 汇总的命中情况。

使用 `--json` 供自动化读取，使用 `--min-recall` 把它变成 CI 门槛。

## 构建原则

- 从真实文档和使用场景出题，不要只问标题或目录。
- 覆盖简单事实、跨页上下文、数值、图表、跨章节综合和聚合统计。
- 表格题必须标注原始单位、百分比符号和小数位。
- 聚合统计题应使用预计算 chunk，不应指望 Top-k 检索逐条召回全文档。
- 每次修改解析器、tokenizer 或 chunk 策略后重新运行并保存结果。
