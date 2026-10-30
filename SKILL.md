---
name: pdf-rag-pipeline
description: 从本地文本型 PDF 构建可溯源、可检索的 RAG 知识层：检测 PDF 类型与分栏，抽取文本块、章节、表格和图片，生成 page/section/table/image chunks，并提供 BM25 检索、读页、表格/图片读取与原文引用。用于 PDF 知识库、合同/财报问答和 RAG 预处理；扫描件 OCR、向量数据库和自动图表理解不在本技能内置。
metadata:
  version: "1.0.0"
  short-description: 将文本型 PDF 转成可溯源 RAG 索引
---

# PDF RAG Pipeline

把 PDF 转成带页码、章节和引用锚点的检索层。默认处理有文本层的 PDF；不要把它描述成已经包含 OCR、embedding 或向量数据库的成品 RAG 服务。

## 先确认适用边界

- 适用：原生文本 PDF、合同、报告、财报、手册，以及需要页码引用的文档问答。
- 部分适用：图文混排 PDF。先提取文本层，再为扫描页或图表单独补充 OCR/视觉描述。
- 不适用：纯扫描件、纯文本/Markdown，或只阅读一两页且不需要建索引的请求。
- 运行依赖 Python 3.10+、PyMuPDF。先运行 `python scripts/check_environment.py`；只有缺少依赖时才安装 `python -m pip install -r requirements.txt`。
- 修改脚本或迁移环境后，运行 `python scripts/selftest.py` 验证完整链路。

## 产出约定

一次完整处理应产出：

- `type.json`：PDF 类型、分栏和解析建议。
- `structure.json`：逐页文本块、标题、表格、图片和来源哈希。
- `chunks.jsonl`：可检索对象，覆盖 `page`、`section`、`table`、`image`。
- `index_meta.json`：索引统计与待补视觉描述数量。
- 检索结果中的 `page`、`chunk_id` 和 `quote_anchor`：用于引用和定位。

## 工作流

以下命令默认当前目录是技能根目录；在其他目录执行时使用脚本的绝对路径。

标准任务优先使用一键入口：

```bash
python scripts/build_pdf_rag.py input.pdf --out-dir rag-out
```

它会依次检测、抽取、切片和质检；检测为 `scanned` 时返回退出码 2，只保留 `type.json`，不会伪造空索引。需要调阈值、检查中间结果或分步排错时，再使用下面的单步命令。

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

默认不硬切正常句子；只有单句超过上限时才做兜底切分。章节上下文会跨页延续，表格和图片继承所在章节。

### 4. 质检和检索

```bash
python scripts/pdf_tools.py stats rag-out/chunks.jsonl
python scripts/pdf_tools.py search rag-out/chunks.jsonl "违约金上限" -k 5
python scripts/pdf_tools.py read rag-out/chunks.jsonl --page 3
python scripts/pdf_tools.py table rag-out/chunks.jsonl --page 2
python scripts/pdf_tools.py image rag-out/chunks.jsonl --page 3
python scripts/pdf_tools.py quote rag-out/chunks.jsonl --chunk-id "<完整 chunk_id>"
```

`table`、`image` 和 `quote` 都支持唯一后缀，例如 `p2-t0` 或 `p3-i0`，但正式引用应保存返回的完整 `chunk_id`。

### 5. 回填视觉描述

先让视觉模型查看图片并生成描述，把映射写入 JSON：

```json
{"p3-i0": "柱状图显示 Q2 最高、Q3 明显下降，单位与图例见原图。"}
```

再执行：

```bash
python scripts/apply_visual_descriptions.py rag-out/chunks.jsonl rag-out/image_descriptions.json
```

脚本会同步更新 `text`、`context_prefix`、`embed_text`、`visual_desc` 和 `needs_visual_desc`。视觉描述必须保留“图像内容来自视觉判断”的不确定性，不要把看不清的数值编出来。

## 回答规则

- 先检索并取得证据，再做回答；不要凭文档标题或文件名猜测内容。
- 简单事实引用 section；数值、金额、比例优先读取 table；图表结论必须使用已回填的视觉描述。
- 回答中至少保留页码和 chunk_id。没有命中时明确说“当前索引中未找到”，不要补造来源。
- 表格数字与原文必须二次核对；页码字段指 PDF 页序号，不一定等于页面上印刷的页码。
- 检索实现是 BM25 + 中文 n-gram，不应宣称是向量语义检索。
- 对 `mixed` 或 `scanned` 文档，明确列出尚未覆盖的页面，不要把局部文本索引当成完整文档索引。

## 参考文件

- [pipeline-spec.md](references/pipeline-spec.md)：体系结构、类型判定和生产边界。
- [chunk-and-metadata.md](references/chunk-and-metadata.md)：chunk 字段、章节和引用约定。
- [retrieval-and-eval.md](references/retrieval-and-eval.md)：检索路由、质量指标和评测方法。
- [toolbox.md](references/toolbox.md)：PyMuPDF、pdfplumber、OCR 和视觉模型的选型边界。
