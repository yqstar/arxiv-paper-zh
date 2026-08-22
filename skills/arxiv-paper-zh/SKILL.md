---
name: arxiv-paper-zh
description: Verify that a requested paper name, acronym, title, authors, abstract, URL, and arXiv ID identify the intended work; then rapidly download and translate its TeX source into Chinese with balanced subagent sharding, deterministic artifact paths under arxiv-paper, paired English/Chinese PDFs, cached dependencies, audits, XeLaTeX compilation, and full-page visual verification. Use when a user asks to locate and translate an arXiv or LaTeX research paper into a compilable Chinese edition, especially for ambiguous acronyms or long papers requiring parallel translation.
---

# arXiv 论文快速中文化

交付保留原始数学、数据、引用和作者信息的中英文可编译源码及经过页面核验的中英文 PDF。身份正确性与译文完整性优先，但要并行执行互不依赖的阶段。

## 输出目录契约

将全部用户产物固定放到 `arxiv-paper/<paper-name>/`。`paper-name` 优先使用用户给出的论文简称并保留大小写，例如 `EST`、`Onetrans`；若用户只给出标题，则核验论文身份后生成简短名称。名称必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]*`，不得包含空格、斜杠或 arXiv ID，除非 ID 本身就是用户指定名称。

```text
arxiv-paper/<paper-name>/
├── latex/
│   ├── source.tar
│   ├── paper-en/
│   └── paper-zh/
├── paper-en/
│   └── <paper-name>-en.pdf
└── paper-zh/
    └── <paper-name>-zh.pdf
```

`latex/paper-en/` 保存未经翻译的解压源码，`latex/paper-zh/` 保存其完整中文副本；两个 PDF 目录只保存最终交付 PDF。开始下载前先运行：

```bash
python3 scripts/prepare_output_layout.py <paper-name> --root arxiv-paper
```

以脚本输出的绝对路径为准，后续不得另建 `work/<arxiv-id>`、顶层 `paper_cn/` 或其他平行交付目录。临时渲染图、构建日志和联系表可放在论文目录下的隐藏临时目录，交付前不得混入 `paper-en/` 或 `paper-zh/`。

## 共享论文翻译运行时

优先复用固定位置的 TinyTeX/TeX Live，不要将运行时复制进每个论文目录。首次建立或主动刷新共享运行时时，使用内置预装清单一次检查并一次批量安装：

```bash
python3 scripts/prepare_tex_runtime.py --preset \
  --kpsewhich <shared-tex-root>/bin/<platform>/kpsewhich \
  --tlmgr <shared-tex-root>/bin/<platform>/tlmgr --install
```

预装清单位于 `references/paper-translation-packages.txt`，覆盖 XeLaTeX 中文排版、BibTeX/Biber、常见数学与字体、表格、算法、代码、绘图、caption，以及 ACM、IEEE、Elsevier、Springer 模板。首次安装需要联网；后续任务先离线检查，只有论文特有依赖缺失时才联网批量补装。不要安装 `scheme-full`。

## 快速流水线

1. 从 arXiv 摘要页或 API 核验规范化 ID、完整标题、作者、摘要、版本和日期。简称不是唯一标识。只有一个候选与用户主题高度一致时才继续，否则先让用户确认。明确告知用户“标题 + 作者 + arXiv ID”。
2. 确定并校验 `paper-name`，运行 `scripts/prepare_output_layout.py`。从 `https://export.arxiv.org/e-print/<ID>` 下载原始压缩包到 `latex/source.tar`，解压到 `latex/paper-en/`。用主 TeX 的 `\title{}`、作者或 README 做第二次身份核验；不一致时停止。
3. 将 `latex/paper-en/` 的全部内容完整复制到 `latex/paper-zh/`，不得产生 `latex/paper-zh/paper-en/` 额外嵌套；此后只修改中文副本。分别定位英文和中文入口文件。
4. 立即并行启动两条路径：

   - 翻译路径：按可见英文词量生成均衡文件分片；正文较多时占满可用 subagent 槽位。每个代理只能编辑明确分配的文件。
   - 构建路径：主代理同时本地化入口/样式文件、准备中文字体并预检 TeX 依赖。不要等待翻译结束后才开始配置编译。

   ```bash
   python3 scripts/inventory_and_shard.py arxiv-paper/<paper-name>/latex/paper-zh --entry main.tex --workers 3 --json
   python3 scripts/prepare_tex_runtime.py arxiv-paper/<paper-name>/latex/paper-zh --preset --kpsewhich /path/to/kpsewhich
   ```

5. 若依赖缺失，使用同一脚本增加 `--tlmgr /path/to/tlmgr --install`，在一次 `tlmgr` 调用中补齐预装包和论文特有包；不得逐个安装。优先复用共享运行时。只有不存在可用运行时时才在可写缓存目录安装便携 TinyTeX；不得使用 `sudo`、`scheme-full` 或完整 TeX Live collection。
6. 主代理修改入口文件以支持 XeLaTeX 中文排版，通常使用 `\usepackage[UTF8,fontset=fandol]{ctex}`。删除仅适用于 pdfLaTeX 的 `inputenc` 和 T1 `fontenc`。本地化模板硬编码的 `Abstract`、`Keywords` 等正文字符串，但保留 `References`/`Bibliography` 标题原文。
7. 翻译参考文献部分以外的所有渲染英文自然语言：标题、摘要、正文、章节标题、列表、脚注、表头、表注和 caption。保留公式、数学符号、数值、引用键、label/ref、URL、LaTeX 结构、人名、模型名、数据集名、缩写与通行技术标识。图片只翻译必要 caption；算法/代码只翻译自然语言注释、docstring、caption 和说明。整个参考文献部分保持原文，包括其标题和全部条目；不得修改 `.bib`、`.bbl`、`thebibliography`/`biblist`/`references` 环境、`\bibitem` 条目或单独的参考文献 TeX 文件。文献综述属于正文，仍需翻译。
8. 子代理完成后，主代理只运行一次全局审计并人工复核全部命中；不得仅扫描顶层章节，也不得把子代理的自检当作最终证明：

   ```bash
   python3 scripts/audit_tex_translation.py arxiv-paper/<paper-name>/latex/paper-zh
   ```

9. 先使用原论文声明或兼容的构建引擎编译 `latex/paper-en/`，确保英文源码、参考文献和交叉引用收敛。再使用自动构建脚本识别 BibTeX/Biber，并完成中文 XeLaTeX 收敛：

   ```bash
   python3 scripts/build_and_check.py arxiv-paper/<paper-name>/latex/paper-zh/main.tex --tex-bin /path/to/tex/bin
   ```

   若仍报告缺包，一次批量安装全部新缺失项后重试。不得接受 LaTeX 错误、未定义引用/citation 或缺失字符。
10. 分别低分辨率渲染中英文 PDF 的全部页面生成联系表，检查裁切、重叠、表格溢出、图片和页数；只对可疑页面高分辨率渲染。另用能正确显示 CJK 的系统 PDF 引擎抽查中文字体。Poppler 缺少 CMap 时不得把空白中文误判为正常。
11. 将英文成品复制为 `paper-en/<paper-name>-en.pdf`，将中文成品复制为 `paper-zh/<paper-name>-zh.pdf`。回复列出完整标题、作者、arXiv ID、`latex/paper-en/`、`latex/paper-zh/` 以及两个 PDF 的绝对路径。

## 并行规则

- 分片前先创建 `latex/paper-zh/`，并按脚本的权重而非文件数分配；脚本默认屏蔽内嵌参考文献区域并排除常见参考文献文件名。
- 入口文件、共享宏和样式只由主代理修改；任何文件不得由两个代理同时编辑。
- 主代理在翻译进行时完成依赖预检、字体方案和编译入口改造。
- 共享运行时只建立一次；论文任务不得重复下载或复制相同运行时。
- 对小论文不强制派发代理；代理启动与合并开销高于预计翻译时间时直接处理。

## 完成标准

- arXiv 元数据与源码身份两次核验通过，原始源码未修改。
- 英文与中文源码分别完整位于 `latex/paper-en/` 和 `latex/paper-zh/`，原始压缩包位于 `latex/source.tar`；参考文献保持原文，其他渲染的英文自然语言均已翻译或被人工判定为允许保留项。
- `paper-en/<paper-name>-en.pdf` 与 `paper-zh/<paper-name>-zh.pdf` 均构建成功，参考文献和交叉引用收敛；中文版无缺字。
- 中英文 PDF 的全部页面均已检查，中文字体由 CJK 能力正常的渲染器确认。

## 方案参考

本 Skill 的论文中文化方案参考了科学空间文章：[《让 AI 翻译一篇完整的论文》](https://spaces.ac.cn/archives/11578)。具体实现结合 Codex 的并行代理、依赖缓存、自动审计和 XeLaTeX 构建流程进行了调整。
