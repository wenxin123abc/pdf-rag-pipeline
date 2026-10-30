# 解析工具选型与能力边界

## 选型矩阵

| 场景 | 首选 | 备选 | 取舍 |
|---|---|---|---|
| 原生文本 PDF，要位置信息 | PyMuPDF（`fitz`/`pymupdf`） | pdfplumber | PyMuPDF 快、块级 bbox 全；pdfplumber 表格能力更强但慢 |
| 多栏排版 | 先由类型检测得到栏数，再显式传 `--columns` | pdfplumber 版面分析 | 本技能支持 1-4 栏的栏带排序；复杂串栏仍需人工抽查 |
| 表格为主（财报、报价单） | pdfplumber | PyMuPDF `find_tables` | `find_tables` 对无线框表格较弱 |
| 扫描件 / 无文本层 | MinerU 等文档解析模型 | 视觉大模型逐页转写 | 文档解析模型批量便宜；视觉模型对复杂版面更灵活 |
| 图文混排（白皮书、研报） | PyMuPDF 抽文本 + 视觉模型补图表 | 统一走多模态解析 | 混合方案省钱，但要按页路由 |
| 超大文档（1000+ 页） | PyMuPDF + 抽样判定 | — | 用 `--max-pages` 抽样判定类型即可，不必全量分析 |

## 各方案的能力边界

**PyMuPDF**
- 强：速度、块级 bbox 与字号、图片定位、表格检测（有框线时）
- 弱：无文本层完全无能为力；复杂无线框表格还原质量一般
- 注意：`import fitz` 是旧名，新版本推荐 `import pymupdf`

**pdfplumber**
- 强：表格抽取精度、字符级坐标、视觉调试友好
- 弱：大文档慢；对扫描件同样无能

**MinerU / 文档智能解析**
- 强：一次拿到文字 + 表格结构 + 图片区域，扫描件也能处理
- 弱：需额外部署或调用；输出结构需适配成统一 chunk

**视觉大模型**
- 强：图表理解、流程图、复杂版面语义
- 弱：贵；同图多次调用结果可能漂移；不适合逐页全量跑

## 多模态补充策略

三个位置必须用视觉模型补，否则索引里就是空洞：

1. **扫描页**：无文本层，只能整页转写
2. **图表**：柱状图/折线图的数值趋势，文本层通常只有图例文字，拿不到结论
3. **示意图 / 流程图**：结构关系文字抽不出来

回填方式：`build_chunks.py` 把 image chunk 标记为 `needs_visual_desc: true`。视觉模型或人工产出 `{image_id: description}` JSON 后，用 `apply_visual_descriptions.py` 把描述写入 `visual_desc` 并重新生成 `embed_text`。检查未回填数量：

```bash
python scripts/pdf_tools.py stats chunks.jsonl
```

## 何时该换方案

| 信号 | 说明 | 换法 |
|---|---|---|
| `doc_type = scanned` | 无文本层 | 上 MinerU / 视觉模型，别硬用 PyMuPDF |
| 表格 chunk 数字对不上原文 | 表格检测失败 | 换 pdfplumber 单独抽表 |
| 检索命中率低但文档明明有 | 图表/扫描页内容没进索引 | 补视觉描述 |
| 多栏文档片段语序混乱 | 未切栏 | `--columns 2` 或先按 x 聚类 |

## 依赖安装

```bash
pip install pymupdf          # 必需（类型识别 + 结构抽取）
pip install pdfplumber       # 表格密集文档时补充
```

内网/离线环境装不上时，改用已自带 PyMuPDF 的解释器（Anaconda 环境常见）。脚本兼容两种导入名，缺失时会直接提示安装命令。
