---
name: arxiv-paper-zh
description: Verify an arXiv or LaTeX paper's identity, then produce complete, compilable English and Chinese sources and PDFs. Use for locating and translating research papers while preserving math, citations, data, and layout; the workflow uses compact protected translation packets, adaptive parallel workers, cached TeX dependencies, audits, XeLaTeX compilation, and full-page visual verification.
---

# arXiv 论文快速中文化

交付保留数学、数据、引用和作者信息的中英文源码与经过页面核验的 PDF。身份正确性和译文完整性优先；翻译阶段使用紧凑任务包，避免将整份 TeX 和重复会话上下文发送给每个 worker。

## 输出目录

全部产物固定放在 `arxiv-paper/<paper-name>/`：

```text
arxiv-paper/<paper-name>/
├── latex/
│   ├── source.tar
│   ├── paper-en/
│   └── paper-zh/
├── paper-en/<paper-name>-en.pdf
└── paper-zh/<paper-name>-zh.pdf
```

`paper-name` 优先使用用户简称并保留大小写，且必须匹配 `[A-Za-z0-9][A-Za-z0-9._-]*`。开始下载前运行：

```bash
python3 scripts/prepare_output_layout.py <paper-name> --root arxiv-paper
```

以脚本输出的绝对路径为准，不另建平行交付目录。原始下载物的唯一允许路径是 `latex/source.tar`；不得另建论文根目录下的 `source/`、`source.tar` 或 `latex/source/` 作为下载、解压中转。若 arXiv 源包自身包含 `source/` 子目录，可原样保留在 `latex/paper-en/` 内。

Agent 自建的下载中转、页面渲染、截图和诊断临时文件全部放在论文根目录的 `tmp/`。失败时保留它用于诊断；只有全部交付物通过最终校验后才删除。`.translation-tasks/` 位于中文源码目录，不得混入最终 PDF 目录。

## 工作流

1. 从 arXiv 摘要页或 API 核验规范化 ID、完整标题、作者、摘要、版本和日期。简称不是唯一标识；候选不唯一时让用户确认。向用户明确标题、作者和 arXiv ID。
2. 创建输出目录，将 `https://export.arxiv.org/e-print/<ID>` 直接保存为脚本返回的 `latex/source.tar`；下载失败时删除不完整文件。直接解压到 `latex/paper-en/`，不得创建额外的 `source/` 中转目录。用主 TeX 的标题、作者或 README 二次核验身份；不一致时停止。
3. 将英文源码内容完整复制到 `latex/paper-zh/`，不得多套一层目录；此后只修改中文副本。分别定位中英文入口文件。
4. 先完成中文入口的 XeLaTeX/ctex 改造和模板可见字符串本地化，再生成翻译任务。通常加入 `\usepackage[UTF8,fontset=fandol]{ctex}`，移除仅适用于 pdfLaTeX 的 `inputenc` 和 T1 `fontenc`。生成任务后、合并任务前不要再编辑中文 TeX 源码，合并器会检查快照。
5. 生成紧凑任务包。脚本包含入口文件，因此单文件论文也能按片段并行；它自动省略参考文献，并用可逆占位符保护公式、引用、URL、代码和注释：

   ```bash
   python3 scripts/translation_tasks.py prepare \
     arxiv-paper/<paper-name>/latex/paper-zh \
     --entry main.tex --workers 3 --json
   ```

   默认 `--workers 3` 是上限；小论文会自动减少 worker，避免启动开销。只有需要改变速度/上下文折中才调整 `--chunk-words` 或 `--min-words-per-worker`。
6. 每个 `worker-*.task` 只交给一个 worker。支持隔离上下文时使用空/最小历史，而不是复制完整会话；任务提示只需：

   ```text
   翻译 <packet> 的全部 SOURCE 区块，把译文填入对应 TRANSLATION 区块。
   严格遵守文件头规则，只编辑该任务包，不读取或修改论文源码。
   ```

   Worker 不需要读取本 Skill、整篇论文或其他任务包。不同 worker 并行编辑各自任务包；不支持 subagent 时顺序处理。主 agent 同时编译英文版、检查中文依赖，但不修改已快照的中文 TeX。
7. Worker 完成后只运行一次合并。合并器先整体校验任务完整性、占位符、LaTeX 结构和源文件哈希，任何错误都会在写文件前停止：

   ```bash
   python3 scripts/translation_tasks.py status arxiv-paper/<paper-name>/latex/paper-zh
   python3 scripts/translation_tasks.py apply arxiv-paper/<paper-name>/latex/paper-zh
   python3 scripts/audit_tex_translation.py arxiv-paper/<paper-name>/latex/paper-zh
   ```

   `status` 未完成时只返工列出的任务；`apply` 报错时只检查对应 segment，不重新读取或重译全部论文。主 agent 必须人工复核全局审计命中。
8. 使用原论文声明或兼容引擎编译英文源码。中文使用自动构建脚本识别 BibTeX/Biber 并完成 XeLaTeX 收敛：

   ```bash
   python3 scripts/build_and_check.py \
     arxiv-paper/<paper-name>/latex/paper-zh/main.tex --tex-bin /path/to/tex/bin
   ```

9. 将中英文页面渲染到 `tmp/render-en/` 和 `tmp/render-zh/`，分别低分辨率检查全部页面的裁切、重叠、溢出、图片和页数；只对可疑页面高分辨率渲染。另用支持 CJK 的系统 PDF 引擎抽查中文字体。Poppler 缺少 CMap 时不得把空白中文误判为正常。
10. 将英文成品复制为 `paper-en/<paper-name>-en.pdf`，中文成品复制为 `paper-zh/<paper-name>-zh.pdf`。最后运行 `python3 scripts/finalize_output.py arxiv-paper/<paper-name>`；它确认 `latex/source.tar`、两套源码和两个 PDF 均非空，拒绝额外的源码中转路径，并仅在校验成功后删除 `tmp/`。成功后再回复论文身份、两套源码目录和两个 PDF 的绝对路径。

## 翻译边界

- 翻译标题、摘要、正文、章节标题、列表、脚注、表头、表注和 caption。
- 保留公式、数学符号、数值、引用键、label/ref、URL、LaTeX 结构、人名、模型名、数据集名、缩写与通行技术标识。
- 图片内部文字默认不修改；算法和代码块（含内嵌注释/docstring）默认保持原样，只翻译 caption 与外部说明。用户明确要求时再单独处理代码内文本。
- 参考文献元数据保持原文；`.bib`、`.bbl`、内嵌 bibliography 环境和独立参考文献 TeX 文件均不进入任务包。
- 不把完整 TeX 内容粘贴进 agent 提示，不让 worker 直接编辑论文文件，也不让多个 worker 处理同一任务包。

## 共享 TeX 运行时

优先复用固定位置的 TinyTeX/TeX Live。首次建立或主动刷新时，使用 `references/paper-translation-packages.txt` 一次检查并批量安装，不安装 `scheme-full`：

```bash
python3 scripts/prepare_tex_runtime.py --preset \
  --kpsewhich <shared-tex-root>/bin/<platform>/kpsewhich \
  --tlmgr <shared-tex-root>/bin/<platform>/tlmgr --install
```

后续先离线检查；论文特有依赖缺失时再用同一脚本和一次 `tlmgr` 调用批量补装。只有没有可用运行时时才在可写缓存目录建立便携 TinyTeX，不使用 `sudo`。

## 完成标准

- arXiv 元数据与源码身份两次核验通过，`latex/paper-en/` 未修改。
- 原始下载包仅存在于非空的 `latex/source.tar`，没有 Agent 创建的 `source/` 或其他源码中转副本。
- 中文任务全部合并，全局漏译审计已人工复核；所有可见自然语言均已翻译或明确允许保留。
- 中英文 PDF 均构建成功，参考文献和交叉引用收敛，中文版无缺字。
- 中英文 PDF 全部页面均已检查，中文字体经 CJK 能力正常的渲染器确认。
- `finalize_output.py` 校验成功，论文根目录的 `tmp/` 已移除。

方案参考科学空间文章[《让 AI 翻译一篇完整的论文》](https://spaces.ac.cn/archives/11578)，并结合紧凑任务包、并行 agent、依赖缓存和自动审计实现。
