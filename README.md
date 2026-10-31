# PDF RAG Pipeline

[![CI](https://github.com/wenxin123abc/pdf-rag-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/wenxin123abc/pdf-rag-pipeline/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

一个面向文本型 PDF 的本地 RAG 预处理、检索和评测工具，也可直接作为 Codex Skill 使用。它保留页码、章节、表格和引用位置，让文档问答能够回到原文，而不是依赖模型猜测。

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
- 支持 1-4 栏左右边缘聚类和页级多数投票，跨页续接表格。
- 生成带完整 metadata 的 JSONL chunk 索引。
- 章节信息跨页延续，表格和图片继承所在章节。
- 支持单 PDF 与批量目录构建，失败文档单独记录。
- 使用带磁盘缓存的 BM25 与中文 unigram/bigram 进行本地检索。
- 支持 NFKC、常用繁简归一化、单字查询和 `--doc` 过滤。
- 支持按页码读页、读取表格、读取图片、获取原文引用。
- 使用 PDF 内容 SHA-256 生成稳定的文档 ID。
- 支持模型无关的图片裁剪队列和视觉描述回填。
- 支持 recall@k、MRR、页码准确率、表格数值一致率评测。
- 支持单文件离线 HTML 原文引用预览和自动结构诊断。
- 自带单元测试、端到端自测和 GitHub Actions CI。

## 工作范围

本项目负责 PDF 的解析、结构化、切片和证据检索。它不是完整的 OCR 或向量数据库服务：

| 能力 | 内置支持 |
|---|---|
| 文本型 PDF | 是 |
| 图文混排文本层 | 是，无文本页需要外部 OCR |
| 纯扫描件 OCR | 否 |
| 本地 BM25 检索 | 是 |
| BM25 倒排缓存 | 是 |
| Embedding / 向量数据库 | 否 |
| 检索效果评测 | 是 |
| bbox 引用预览 | 是 |
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

批量目录：

```bash
python scripts/build_corpus.py ./pdfs --out corpus --recursive
```

批量构建不会因为单个损坏或扫描文档中断。错误写入 `corpus/errors.jsonl`，每份成功文档保留独立中间产物。

输出目录包含：

| 文件 | 用途 |
|---|---|
| `type.json` | PDF 类型、分栏、图片覆盖率与风险提示 |
| `structure.json` | 逐页文本块、标题、表格、图片和来源哈希 |
| `chunks.jsonl` | 可检索的 page/section/table/image chunks |
| `index_meta.json` | 索引统计与待补视觉描述数量 |

首次检索会在 `chunks.jsonl` 旁生成内容哈希命名的 BM25 倒排缓存。索引内容变化后自动失效并重建，不引入 pickle 反序列化风险。

## 检索和引用

```bash
python scripts/pdf_tools.py stats rag-out/chunks.jsonl
python scripts/pdf_tools.py search rag-out/chunks.jsonl "预付款比例" --type table -k 5
python scripts/pdf_tools.py search corpus/chunks.jsonl "付款" --doc "Alpha"
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

先生成裁剪图片和模型无关的视觉提示词：

```bash
python scripts/visual_queue.py rag-out/chunks.jsonl --pdf input.pdf --out rag-out/visual-queue
```

把多模态模型结果填入 `visual-queue/descriptions.template.json`：

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

## 检索评测

从当前索引生成 30 条模板：

```bash
python scripts/eval.py --template-out rag-out/labels.template.jsonl rag-out/chunks.jsonl
```

填写问题、gold chunk、页码和表格期望值后：

```bash
python scripts/eval.py labels.jsonl rag-out/chunks.jsonl --k 5 --min-recall 0.9
```

输出：

- `recall@k`
- `MRR`
- 页码准确率
- 表格数值一致率
- 按题型拆分结果

详细字段见 [references/eval-labels.md](references/eval-labels.md)。

## 离线引用预览

```bash
python scripts/preview.py rag-out/chunks.jsonl --pdf input.pdf --out rag-out/preview.html --query "违约金上限"
```

页面图片、CSS、JavaScript 和 bbox 高亮全部嵌入单个 HTML，可离线打开。点击检索片段会在右侧跳到对应 PDF 页并高亮证据区域。

## 结构与索引诊断

```bash
python scripts/inspect_index.py rag-out/structure.json rag-out/chunks.jsonl
```

检查标题比例、疑似漏表、超长 chunk、章节路径缺失和页眉页脚剔除异常。加 `--strict` 可在 CI 中发现问题时失败。

## 为什么不是又一个 RAG Demo

| 维度 | PDF 转文本切片 | 通用 RAG 框架 | 本项目 |
|---|---|---|---|
| 页码与 bbox | 常丢失 | 取决于适配器 | 内建 |
| 跨页章节 | 常丢失 | 需自行实现 | 内建 |
| 表格证据 | 常被切碎 | 需接解析器 | 独立 table chunk |
| 图像处理 | 常忽略 | 需多模态插件 | 裁剪队列 + 描述回填 |
| 引用预览 | 需前端 | 需二次开发 | 单文件离线 HTML |
| 检索质量 | 凭感觉 | 需配置评测 | recall/MRR/页码/数值指标 |
| 覆盖缺口 | 容易静默 | 容易静默 | 诊断器和稳定退出码 |

项目刻意不内置 LLM 问答层、向量数据库或 SaaS 前端。它专注于最脏的一层：把 PDF 变成带坐标、可核验的证据。

## 退出码

| 退出码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 参数、输入、质量门槛或一般执行错误 |
| `2` | 检测为扫描件 |
| `3` | 加密、密码错误或损坏 |
| `4` | 没有可提取文本 |

自动化流程应依赖退出码，而不是匹配中文错误消息。

## 性能预期

以下是数量级，不是固定 SLA：

- 开发机 100 页纯文本合成 PDF（无扫描/表格）：约 `1.8s`，`structure.json` 约 `100 KB`，`chunks.jsonl` 约 `346 KB`。
- 真实合同、财报和大表格 PDF 可能达到数十秒。
- 大表格、多栏和复杂图片会显著增加时间。
- 开发机合成 5000 chunk 基准：首次 BM25 缓存约 `0.10s`，缓存加载约 `0.01s`，单次打分约 `0.003s`。
- `structure.json` 包含 bbox、字号和块文本，通常大于 `chunks.jsonl`。

## 什么时候不该用

- 只读一两页：直接读取 PDF 更快。
- 输入已经是纯文本或 Markdown：不需要 PDF 版面恢复。
- 纯扫描件：先接 OCR/MinerU/多模态文档解析。
- 需要向量语义检索：可把 `embed_text` 与 metadata 接入外部向量库；本项目只提供 BM25 兜底。

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
python -m unittest discover -s tests -v
python scripts/selftest.py
```

单元/集成测试覆盖：

- 多栏聚类与页级投票
- 题注去重和跨页表格
- 短文档页眉保护与超长文本切分
- 加密/空白 PDF 退出码
- 批量语料和错误隔离
- BM25 缓存、失效和性能
- NFKC、繁简归一化与单字查询
- 评测指标
- 视觉队列、离线预览和结构诊断

端到端自测会生成临时 PDF，并验证：

- PDF 类型检测
- 双栏检测
- 三栏检测与阅读顺序
- 扫描件拒绝路径
- 文本块、标题和表格抽取
- 跨页章节延续
- chunk 生成与检索
- 空检索结果
- 表格、图片与引用命令
- 视觉描述回填
- 视觉描述队列
- 检索评测
- 离线引用预览
- 结构诊断
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
