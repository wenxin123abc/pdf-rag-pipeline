# PDF RAG Pipeline

[![CI](https://github.com/wenxin123abc/pdf-rag-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/wenxin123abc/pdf-rag-pipeline/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

一个面向文本型 PDF 的本地 RAG 预处理与检索工具，也可直接作为 Codex Skill 使用。它保留页码、章节、表格和引用位置，让文档问答能够回到原文，而不是依赖模型猜测。

## 解决的问题

把 PDF 简单转成纯文本再切片，通常会丢失：

- 页码和版面位置
- 章节层级
- 表格行列结构
- 图片与题注
- 跨页上下文
- 可点击、可核对的引用依据

本项目把 PDF 转成 `page`、`section`、`table`、`image` 四类可检索对象，并提供本地关键词检索、读页、查表、查图和原文引用能力。

## 核心能力

- 检测文本型、扫描型、图文混排 PDF，并估计 1-4 栏布局。
- 基于 PyMuPDF 提取文本块、bbox、字号、标题、表格和图片。
- 合并同行碎片、重建段落、识别重复页眉页脚。
- 生成带完整 metadata 的 JSONL chunk 索引。
- 章节信息跨页延续，表格和图片继承所在章节。
- 使用 BM25 与中文 unigram/bigram 进行本地检索。
- 支持按页码读页、读取表格、读取图片、获取原文引用。
- 使用 PDF 内容 SHA-256 生成稳定的文档 ID。
- 支持合并外部视觉模型生成的图片描述。
- 自带环境检查、端到端自测和 GitHub Actions CI。

## 工作范围

本项目负责 PDF 的解析、结构化、切片和证据检索。它不是完整的 OCR 或向量数据库服务：

| 能力 | 内置支持 |
|---|---|
| 文本型 PDF | 是 |
| 图文混排文本层 | 是，无文本页需要外部 OCR |
| 纯扫描件 OCR | 否 |
| 本地 BM25 检索 | 是 |
| Embedding / 向量数据库 | 否 |
| 自动图表理解 | 否，支持回填外部视觉描述 |
| LLM 问答与前端 | 否 |

检测到纯扫描件时，一键流程会停止并返回退出码 `2`，不会生成看似成功的空索引。

## 快速开始

要求 Python 3.10+。

```bash
git clone https://github.com/wenxin123abc/pdf-rag-pipeline.git
cd pdf-rag-pipeline
python -m pip install -r requirements.txt
python scripts/check_environment.py
```

处理 PDF：

```bash
python scripts/build_pdf_rag.py input.pdf --out-dir rag-out
```

输出目录包含：

| 文件 | 用途 |
|---|---|
| `type.json` | PDF 类型、分栏、图片覆盖率与风险提示 |
| `structure.json` | 逐页文本块、标题、表格、图片和来源哈希 |
| `chunks.jsonl` | 可检索的 page/section/table/image chunks |
| `index_meta.json` | 索引统计与待补视觉描述数量 |

## 检索和引用

```bash
python scripts/pdf_tools.py stats rag-out/chunks.jsonl
python scripts/pdf_tools.py search rag-out/chunks.jsonl "预付款比例" --type table -k 5
python scripts/pdf_tools.py read rag-out/chunks.jsonl --page 2
python scripts/pdf_tools.py table rag-out/chunks.jsonl --page 2
python scripts/pdf_tools.py image rag-out/chunks.jsonl --page 3
python scripts/pdf_tools.py quote rag-out/chunks.jsonl --chunk-id "<chunk_id>"
```

检索结果包含：

- `page`：PDF 页序号
- `chunk_id`：唯一证据 ID
- `quote_anchor`：页码、bbox 或表格/图片定位
- `snippet`：命中片段

正式引用应保存完整 `chunk_id`。表格、图片和引用命令也支持唯一后缀，例如 `p2-t0`。

## 图片描述回填

先让视觉模型查看图片，生成如下 JSON：

```json
{
  "p3-i0": "柱状图显示 Q2 最高、Q3 明显下降；具体数值应以原图为准。"
}
```

再合并到索引：

```bash
python scripts/apply_visual_descriptions.py rag-out/chunks.jsonl image_descriptions.json
```

脚本会同步更新 `text`、`embed_text`、`visual_desc`、`needs_visual_desc` 和 `index_meta.json`。

## 作为 Codex Skill 安装

Windows PowerShell：

```powershell
git clone https://github.com/wenxin123abc/pdf-rag-pipeline.git "$env:USERPROFILE\.codex\skills\pdf-rag-pipeline"
```

macOS / Linux：

```bash
git clone https://github.com/wenxin123abc/pdf-rag-pipeline.git ~/.codex/skills/pdf-rag-pipeline
```

重新打开 Codex 后，可通过 `$pdf-rag-pipeline` 调用。

## 分步工作流

需要调参数或排错时，可以逐步执行：

```bash
python scripts/detect_pdf_type.py input.pdf --out rag-out/type.json
python scripts/extract_structure.py input.pdf --out rag-out/structure.json --columns 1
python scripts/build_chunks.py rag-out/structure.json --out rag-out/chunks.jsonl --max-chars 900
python scripts/pdf_tools.py stats rag-out/chunks.jsonl
```

复杂无线框表格可以改用 pdfplumber 或文档解析模型补表，再转换成统一结构。

## 测试

```bash
python scripts/selftest.py
```

自测会生成临时 PDF，并验证：

- PDF 类型检测
- 双栏检测
- 扫描件拒绝路径
- 文本块、标题和表格抽取
- 跨页章节延续
- chunk 生成与检索
- 空检索结果
- 表格、图片与引用命令
- 视觉描述回填
- 一键构建流程

## 设计原则

```text
PDF
  -> 类型与版面检测
  -> 结构还原
  -> page / section / table / image chunks
  -> 本地 BM25 检索
  -> page + chunk_id + anchor
  -> 可核对的回答
```

- 检索使用 `embed_text`，引用使用原文 `text`。
- 页码来自 PDF 页序号，不一定等于页面印刷页码。
- 没有命中时返回“未找到”，不退化为无关片段。
- 表格数字必须回到 table chunk 二次核对。
- 扫描页和图表没有被覆盖时，必须明确说明覆盖缺口。

## 贡献

欢迎提交 Issue 和 Pull Request。涉及解析行为的改动应同时补充自测，并说明适用的 PDF 类型、测试样例和剩余限制。

## License

[MIT](LICENSE)
