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

继续同一论文时，若中文源码已有 `.translation-tasks/manifest.json`，先运行 `translation_tasks.py resume <中文源码目录> --json`，按返回的 `next_action` 继续；不要重新下载、覆盖中文副本或强制生成任务。恢复与修复命令见 [references/translation-recovery.md](references/translation-recovery.md)，仅在续跑、修复或合并中断时读取。

1. 从 arXiv 摘要页或 API 核验规范化 ID、完整标题、作者、摘要、版本和日期。简称不是唯一标识；候选不唯一时让用户确认。向用户明确标题、作者和 arXiv ID。
2. 创建输出目录，将 `https://export.arxiv.org/e-print/<ID>` 直接保存为脚本返回的 `latex/source.tar`；下载失败时删除不完整文件。直接解压到 `latex/paper-en/`，不得创建额外的 `source/` 中转目录。用主 TeX 的标题、作者或 README 二次核验身份；不一致时停止。
3. 将英文源码内容完整复制到 `latex/paper-zh/`，不得多套一层目录；此后只修改中文副本。分别定位中英文入口文件。
4. 先完成中文入口的 XeLaTeX/ctex 改造和模板可见字符串本地化，再生成翻译任务。通常加入 `\usepackage[UTF8,fontset=fandol]{ctex}`，移除仅适用于 pdfLaTeX 的 `inputenc` 和 T1 `fontenc`。生成任务后、合并任务前不要再编辑中文 TeX 源码，合并器会检查快照。
5. 生成紧凑任务包。脚本包含入口文件，因此单文件论文也能按片段并行；它自动省略参考文献，并用可逆占位符保护公式、引用、URL、代码和注释：

   ```bash
   python3 scripts/translation_tasks.py prepare \
     arxiv-paper/<paper-name>/latex/paper-zh \
     --entry main.tex --workers 3 --packet-words 2000 --json
   ```

   `--packet-words 2000` 限制每包的估算可见英文词数，`--chunk-words 900` 是包内片段的目标词数，均不是模型 token 数。脚本将相邻片段依次装包，任务包数可超过 worker 数；`--workers 3` 仅为同时运行的 worker 上限，小论文自动减少。单个片段超出包上限时，脚本在生成任务包前报出位置；先检查并调整该处源码换行，或明确调大包上限后重新 prepare。
6. 每个只读 `packet-*.task` 同时只交给一个 worker，译文写入对应 `packet-*.result.jsonl`。支持隔离上下文时使用空/最小历史；任务提示只需：

   ```text
   读取 <packet>，将全部 SOURCE 区块译为简体中文，结果写入 <result>。
   严格遵守文件头规则；只写结果文件，不修改任务包或读取、修改论文源码。
   结果用 JSONL，每行仅含 id 和 translation；完成后只返回结果路径和完成片段数。
   ```

   Worker 不需要读取本 Skill、完整 manifest 或其他任务包，不回显原文与译文。主 agent 按 prepare/status 返回的下一批任务调度，记录正在处理的任务，避免重复分配。每包完成后运行 `translation_tasks.py check <中文源码目录> --packet <packet> --json`，立即校验格式、ID、占位符、LaTeX 结构和源码快照，保存校验断点；失败时用 `repair` 生成仅含错误片段的修复包，修复后用 `repair --apply` 接收结果。空闲 worker 继续领取下一包，上下文过长时换用新 worker。不支持 subagent 时顺序处理。主 agent 同时编译英文版、检查中文依赖，但不修改已快照的中文 TeX。
7. 全部任务校验通过后统一合并。合并前再次确认输入哈希，先暂存完整写入结果并记录合并日志，再替换源码；中断后由 `resume` 完成剩余写入，重复 `apply` 不会再次替换已合并的源码：

   ```bash
   python3 scripts/translation_tasks.py status arxiv-paper/<paper-name>/latex/paper-zh
   python3 scripts/translation_tasks.py apply arxiv-paper/<paper-name>/latex/paper-zh
   python3 scripts/audit_tex_translation.py arxiv-paper/<paper-name>/latex/paper-zh
   ```

   `status` 的 `completed/validated` 计数表示通过结构校验的片段；新源码、结果或校验规则会使旧校验缓存失效。默认只输出摘要和下一批任务，完整列表用 `--details`。这些检查不判断译文语义质量，主 agent 仍须复核漏译审计；命中超过 10 条时用 `audit_tex_translation.py ... --details` 查看全部。版本 1/2 的任务仍可继续，不为格式升级重做译文。
8. 使用构建脚本分别编译中英文入口。英文用 `--engine pdflatex`、`xelatex` 或 `lualatex` 选择论文兼容引擎；中文默认 XeLaTeX。脚本根据辅助文件识别 BibTeX/Biber，引用与辅助文件稳定后结束，最多 6 轮，未收敛则失败：

   ```bash
   python3 scripts/build_and_check.py \
     arxiv-paper/<paper-name>/latex/paper-zh/main.tex --tex-bin /path/to/tex/bin
   ```

   同一源码、已记录依赖与成品哈希匹配时复用成功构建；正文修改但文献输入不变时可跳过文献处理器。默认返回轮数、缓存命中与耗时，完整日志保存在论文 `tmp/`；诊断时按需读取或用 `--verbose`。缓存范围、失效条件及参数见 [references/build-and-render.md](references/build-and-render.md)，进入编译或页面检查时读取。
9. 用 `render_pdf.py <PDF> --output <论文目录>/tmp/render-zh --json` 渲染中文版，英文改用 `tmp/render-en`。默认以 90 DPI 渲染全部页面；使用返回的 `render_dir` 检查全部页面的裁切、重叠、溢出、图片和页数，不混用旧目录。PDF、DPI 或渲染器变化时使用独立缓存，图片缺失或哈希不符时只补对应页面。可疑页用 `--dpi 180 --pages 2,5-6` 单独渲染。图片缓存不代表已完成视觉检查；另用支持 CJK 的系统 PDF 引擎抽查中文字体。Poppler 缺少 CMap 时不得把空白中文误判为正常；修复字体/CMap 后加 `--force` 重渲染并复查。
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

后续先离线检查；论文特有依赖缺失时再用同一脚本和一次 `tlmgr` 调用批量补装。默认只输出检查数量、缺包名称和安装结果，安装日志保留在本地；传入标准论文源码路径时放在论文 `tmp/`，仅预装共享环境时放在系统临时目录。`--verbose` 可显示完整包清单和安装输出。只有没有可用运行时时才在可写缓存目录建立便携 TinyTeX，不使用 `sudo`。

## 完成标准

- arXiv 元数据与源码身份两次核验通过，`latex/paper-en/` 未修改。
- 原始下载包仅存在于非空的 `latex/source.tar`，没有 Agent 创建的 `source/` 或其他源码中转副本。
- 中文任务全部合并，全局漏译审计已人工复核；所有可见自然语言均已翻译或明确允许保留。
- 中英文 PDF 均构建成功，参考文献和交叉引用收敛，中文版无缺字。
- 中英文 PDF 全部页面均已检查，中文字体经 CJK 能力正常的渲染器确认。
- `finalize_output.py` 校验成功，论文根目录的 `tmp/` 已移除。

方案参考科学空间文章[《让 AI 翻译一篇完整的论文》](https://spaces.ac.cn/archives/11578)，并结合紧凑任务包、并行 agent、依赖缓存和自动审计实现。
