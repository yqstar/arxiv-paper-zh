# 编译收敛与页面缓存

以下命令中的 `PAPER` 表示论文根目录，例如 `arxiv-paper/EST`。缓存放在 `PAPER/tmp/`，供同一任务重试或恢复时复用；`finalize_output.py` 成功后会一并清理。缓存可随时重建，不作为交付物。

## 构建

```bash
python3 scripts/build_and_check.py PAPER/latex/paper-en/main.tex --engine pdflatex --tex-bin /path/to/tex/bin --json
python3 scripts/build_and_check.py PAPER/latex/paper-zh/main.tex --tex-bin /path/to/tex/bin --json
```

英文引擎按论文模板选择，中文默认 XeLaTeX。非标准目录默认以入口所在目录为源码根；入口在子目录而图表等位于上层时，用 `--source-root` 指定完整源码目录。构建工作目录仍为入口所在目录。

脚本开启 `-recorder`，比较 `.aux`、目录和文献等辅助文件，并检查日志中的未定义引用、重跑提示、TeX 错误与缺字。辅助文件稳定且日志无上述问题才成功。默认最多 6 轮；确需更多时显式传入 `--max-runs`，不要把达到上限当成构建成功。

BibTeX 从入口与递归引用的 `.aux` 读取文献命令，Biber 从 `.bcf` 读取控制信息。控制信息、解析到的 `.bib/.bst`、文献工具和 `.bbl` 均不变时可以省略文献处理；缺少 `.bbl` 或文献输入改变时重新生成。

成功记录保存在 `tmp/.build-cache/`。下列内容均匹配才会跳过整个构建：源码根内的可见输入文件、编译器与脚本内容、相关 TeX 环境变量、`.fls` 记录的实际外部依赖，以及 PDF、TeX 日志和辅助文件哈希。没有依赖记录、或文献输入无法定位时，允许完成编译但禁用该次缓存，并返回原因。失败构建不会生成成功记录。

动态库、系统字体配置以及新安装文件改变搜索路径优先级，并不一定体现为已记录文件内容变化。更新 TeX 环境或字体后用 `--force` 强制构建（包括文献处理）。这也适用于含时间、随机数或外部命令的模板。首次构建或强制构建仍执行全部收敛检查。

默认摘要包含 `cached`、`runs`、`bibliography_runs`、`elapsed_seconds` 和 `build_log`。`cached=true` 时两项轮数为 0，保留原日志。`--json` 的 stdout 为结构化结果；`--verbose` 同时显示完整命令输出，组合使用时诊断写到 stderr。标准布局日志为 `tmp/paper-en-build.log` 或 `tmp/paper-zh-build.log`；独立入口默认 `<stem>.build.log`，可通过 `--log-file` 指定，缓存跟随日志目录。

## 页面渲染

需要 Poppler 的 `pdfinfo` 与 `pdftoppm`；不在 PATH 中时使用 `--pdfinfo /path/to/pdfinfo --pdftoppm /path/to/pdftoppm`。

```bash
# 全页低分辨率检查，中英文分别执行
python3 scripts/render_pdf.py PAPER/latex/paper-en/main.pdf --output PAPER/tmp/render-en --json
python3 scripts/render_pdf.py PAPER/latex/paper-zh/main.pdf --output PAPER/tmp/render-zh --json

# 仅对疑似问题页增加分辨率
python3 scripts/render_pdf.py PAPER/latex/paper-zh/main.pdf --output PAPER/tmp/render-zh --dpi 180 --pages 2,5-6 --json
```

每个 PDF 内容、DPI、工具与脚本版本组合使用独立的 `render_dir`，页面名为 `page-0001.png` 等。以本次返回目录为准；不要把缓存根目录下不同版本的 PNG 混在一起检查。默认列出最多 3 个示例路径，`requested_count` 是实际请求页数，`--details` 可列出全部路径。

首次全页渲染只启动一次 `pdftoppm`。后续逐页检查图片哈希，完整页面直接复用，缺失或损坏页按连续范围批量补渲染。每个范围成功后保存断点；后续范围失败时，已完成范围仍能复用。PDF 在渲染期间变化会报错，失败日志和暂存图片留在 `render_dir` 便于诊断。

摘要提供 `rendered`、`reused` 和 `elapsed_seconds`。它们表示本次实际生成、复用的图片数量及脚本耗时，不表示视觉检查已通过，也不是模型 token 节省比例。仍须低分辨率检查全部页面、复查疑似页，并使用 CJK 正常的另一 PDF 引擎抽查中文字体。字体、CMap 或 Poppler 运行库修复后用 `--force`；仅给出 `--pages` 时强制更新这些页，要更新整份 PDF 则不传页码范围。
