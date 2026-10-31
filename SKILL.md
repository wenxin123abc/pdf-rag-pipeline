---
name: pdf-rag-pipeline
description: 从本地文本型 PDF 构建可溯源、可评测的 RAG 知识层：支持单文档或批量语料、1-4 栏结构还原、跨页章节和表格、页/段/表/图 chunks、缓存 BM25 检索、召回评测、视觉描述队列和离线引用预览。扫描件 OCR、向量数据库和自动图表理解不在本技能内置。
metadata:
  version: "1.1.0"
  short-description: 将文本型 PDF 转成可溯源 RAG 索引
---

# PDF RAG Pipeline

把 PDF 转成带页码、章节和引用锚点的检索层。默认处理有文本层的 PDF；不要把它描述成已经包含 OCR、embedding 或向量数据库的成品 RAG 服务。

## 先确认适用边界

- 适用：原生文本 PDF、合同、报告、财报、手册，以及需要页码引用的文档问答。
- 部分适用：图文混排 PDF。先提取文本层，再为扫描页或图表单独补充 OCR/视觉描述。
- 不适用：纯扫描件、纯文本/Markdown 输入，或只需阅读一两页且不需要建立可复用索引的请求。这些任务直接读原文件更快，也能避免页眉页脚和版面启发式造成误伤。
- 运行依赖 Python 3.10+、PyMuPDF。先运行 `python scripts/check_environment.py`；只有缺少依赖时才安装 `python -m pip install -r requirements.txt`。
- 修改脚本或迁移环境后，运行 `python -m unittest discover -s tests -v` 和 `python scripts/selftest.py` 验证完整链路。

## 产出约定

一次完整处理应产出：

- `type.json`：PDF 类型、分栏和解析建议。
- `structure.json`：逐页文本块、标题、表格、图片和来源哈希。
- `chunks.jsonl`：可检索对象，覆盖 `page`、`section`、`table`、`image`。
- `index_meta.json`：索引统计与待补视觉描述数量。
- 检索结果中的 `page`、`chunk_id` 和 `quote_anchor`：用于引用和定位。
- 批量任务还会生成 `corpus_meta.json`、`errors.jsonl` 和每份文档的中间产物。

## 工作流

以下命令默认当前目录是技能根目录；在其他目录执行时使用脚本的绝对路径。

标准任务优先使用一键入口：

```bash
python scripts/build_pdf_rag.py input.pdf --out-dir rag-out
```

批量知识库：

```bash
python scripts/build_corpus.py ./pdfs --out corpus --recursive
```

批量入口逐份处理，损坏或扫描文档写入 `corpus/errors.jsonl`，不中断其他文件；最终 chunks 合并到 `corpus/chunks.jsonl`。

一键入口会依次检测、抽取、切片、统计和结构诊断。检测为 `scanned` 时返回退出码 2，只保留 `type.json`，不会伪造空索引。需要调阈值、检查中间结果或分步排错时，再使用下面的单步命令。

### 1. 检测 PDF

```bash
python scripts/detect_pdf_type.py input.pdf --out rag-out/type.json
```

读取 `doc_type`、`columns.max_detected`、`stats` 和 `warnings`。

- `text`：继续标准流程。
- `mixed`：继续提取文本层，但标记无文本页和图表需要 OCR/视觉补充。
- `scanned`：停止本技能的文本管线，改用 OCR 或多模态文档解析；不要生成空索引后宣称处理完成。
- `columns.max_detected > 1`：把同一个值传给下一步的 `--columns`。

### 2. 还原结构

```bash
python scripts/extract_structure.py input.pdf --out rag-out/structure.json --columns 1
```

抽查 `structure.json`：标题数不应接近文本块总数；表格应保留行列；重复页眉页脚应被剔除。复杂或无线框表格质量不足时，改用 pdfplumber/文档解析模型补表，并把结果合并回统一结构。

### 3. 生成 chunks

```bash
python scripts/build_chunks.py rag-out/structure.json --out rag-out/chunks.jsonl --max-chars 900
```

默认不硬切正常句子；只有单句超过上限时才做兜底切分。章节上下文会跨页延续，表格和图片继承所在章节。确实无法在句子、子句或词边界内切开的内容会标记 `truncated: true`，`pdf_tools.py stats` 会报告数量。

### 4. 质检和检索

```bash
python scripts/pdf_tools.py stats rag-out/chunks.jsonl
python scripts/pdf_tools.py search rag-out/chunks.jsonl "违约金上限" -k 5
python scripts/pdf_tools.py search rag-out/chunks.jsonl "付款" --doc "<doc_id 或标题关键词>"
python scripts/pdf_tools.py read rag-out/chunks.jsonl --page 3
python scripts/pdf_tools.py table rag-out/chunks.jsonl --page 2
python scripts/pdf_tools.py image rag-out/chunks.jsonl --page 3
python scripts/pdf_tools.py quote rag-out/chunks.jsonl --chunk-id "<完整 chunk_id>"
```

`table`、`image` 和 `quote` 都支持唯一后缀，例如 `p2-t0` 或 `p3-i0`，但正式引用应保存返回的完整 `chunk_id`。
检索词会做 NFKC、常用繁简归一化和中文 unigram/bigram 处理。首次查询会在索引旁生成内容哈希命名的 BM25 缓存，源索引变化后自动失效重建。

结构诊断：

```bash
python scripts/inspect_index.py rag-out/structure.json rag-out/chunks.jsonl
```

诊断会检查标题比例、疑似漏表、超长 chunk、章节路径缺失和页眉页脚剔除异常。`--strict` 在发现问题时返回退出码 1。

### 5. 回填视觉描述

先生成图片裁剪和模型无关的提示词队列：

```bash
python scripts/visual_queue.py rag-out/chunks.jsonl --pdf input.pdf --out rag-out/visual-queue
```

`prompts.json` 可直接交给任意多模态模型。把结果填入 `descriptions.template.json`：

```json
{"p3-i0": "柱状图显示 Q2 最高、Q3 明显下降，单位与图例见原图。"}
```

再执行：

```bash
python scripts/apply_visual_descriptions.py rag-out/chunks.jsonl rag-out/image_descriptions.json
```

脚本会同步更新 `text`、`context_prefix`、`embed_text`、`visual_desc` 和 `needs_visual_desc`。视觉描述必须保留“图像内容来自视觉判断”的不确定性，不要把看不清的数值编出来。

### 6. 评测与引用预览

从索引生成 30 条标注模板：

```bash
python scripts/eval.py --template-out rag-out/labels.template.jsonl rag-out/chunks.jsonl
```

填写真实问题后运行：

```bash
python scripts/eval.py labels.jsonl rag-out/chunks.jsonl --k 5 --min-recall 0.9
```

输出 `recall@k`、`MRR`、页码准确率、表格数值一致率和按题型拆分结果。不要只凭几个成功示例宣称检索质量。

生成离线单文件预览：

```bash
python scripts/preview.py rag-out/chunks.jsonl --pdf input.pdf --out rag-out/preview.html --query "违约金上限"
```

页面图像、样式和 bbox 高亮都嵌入 HTML，不依赖网络。

## 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 参数、输入、质量门槛或一般执行错误 |
| `2` | 检测为扫描件，本技能拒绝伪造文本索引 |
| `3` | PDF 加密、密码错误或文件损坏 |
| `4` | PDF 可打开但没有可提取文本 |

上层自动化应依赖退出码，不应解析自然语言错误消息。

## 性能预期

以下是数量级而非固定 SLA，实际取决于 CPU、PyMuPDF、表格密度和磁盘：

- 开发机 100 页纯文本合成 PDF（无表格/扫描）：约 1.8 秒，`structure.json` 约 100 KB，`chunks.jsonl` 约 346 KB。
- 真实合同/财报因表格、多栏、OCR 和视觉内容可能达到数十秒。
- 首次处理主要包括解析、表格检测和结构 JSON 写入；大表格会显著增加时间。
- 开发机合成 5000 chunk 基准：首次 BM25 缓存约 0.10 秒，缓存加载约 0.01 秒，单次打分约 0.003 秒。
- `structure.json` 包含 bbox、字号和块文本，通常明显大于最终 `chunks.jsonl`。

## 回答规则

- 先检索并取得证据，再做回答；不要凭文档标题或文件名猜测内容。
- 简单事实引用 section；数值、金额、比例优先读取 table；图表结论必须使用已回填的视觉描述。
- 回答中至少保留页码和 chunk_id。没有命中时明确说“当前索引中未找到”，不要补造来源。
- 表格数字与原文必须二次核对；页码字段指 PDF 页序号，不一定等于页面上印刷的页码。
- 检索实现是缓存 BM25 + 中文 n-gram，不应宣称是向量语义检索。
- 对 `mixed` 或 `scanned` 文档，明确列出尚未覆盖的页面，不要把局部文本索引当成完整文档索引。

## 参考文件

- [pipeline-spec.md](references/pipeline-spec.md)：体系结构、类型判定和生产边界。
- [chunk-and-metadata.md](references/chunk-and-metadata.md)：chunk 字段、章节和引用约定。
- [retrieval-and-eval.md](references/retrieval-and-eval.md)：检索路由、质量指标和评测方法。
- [eval-labels.md](references/eval-labels.md)：评测标签字段、命令和结果解释。
- [toolbox.md](references/toolbox.md)：PyMuPDF、pdfplumber、OCR 和视觉模型的选型边界。
