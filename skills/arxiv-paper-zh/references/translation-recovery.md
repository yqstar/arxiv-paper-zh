# 翻译校验、局部修复与续跑

以下命令的 `ROOT` 均指中文源码目录，例如 `arxiv-paper/EST/latex/paper-zh`。`PACKET` 是清单返回的任务包名称或绝对路径。主 agent 运行校验与恢复命令；worker 只读分配的任务包并写入对应结果文件。

## 每包完成后

```bash
python3 scripts/translation_tasks.py check ROOT --packet PACKET --json
```

返回 `packet_validated` 后继续调度。这个检查只针对指定包，不要求其他 worker 已完成；同一输入、结果和校验规则的检查会复用 `.checks/` 中的断点。状态里的通过计数仅代表格式和结构有效，不等于语义、漏译或视觉审查已完成。

## 只修复出错片段

```bash
python3 scripts/translation_tasks.py repair ROOT --packet PACKET --json
```

将返回的只读 `path` 和 `result_path` 交给一个 worker；路径均相对 `.translation-tasks/`。修复包只含失败或缺失片段的 `SOURCE`、`CURRENT` 和 `ERRORS`。过长的异常结果可能截断，完整原文始终保留。正确片段保存在原结果文件，不让 worker 重读或重译。摘要里的 ID 默认最多显示 5 个，`segment_count` 表示实际数量；worker 处理修复包中的全部片段，需要完整机器列表时加 `--details`。

Worker 按包头规则输出 JSONL，只包含本次要求的 ID 和修正译文。主 agent 接收：

```bash
python3 scripts/translation_tasks.py repair ROOT --packet PACKET --apply --json
python3 scripts/translation_tasks.py check ROOT --packet PACKET --json
```

接收前再次确认原结果、源码和修复任务包未变化，并检查修正结果。只有整份修复通过才替换原结果，保留正确片段的译文。重复生成同一个修复不会清空已写入的修复结果。若所有已知 ID 都正确、仅存在无归属的多余行，`repair` 会备份原结果并直接规范化，无需模型翻译。

源码或只读包被修改属于输入冲突，不生成修复包。先查明变化原因；不要通过 `--force` 清除断点来掩盖冲突。修复连续两次仍因同一原因失败时，主 agent 查看对应片段并处理根因，避免无限分派相同任务。

## 中断后继续

```bash
python3 scripts/translation_tasks.py resume ROOT --json
# 已有任务时的等价入口；复用原有分包参数
python3 scripts/translation_tasks.py prepare ROOT --resume --json
```

按返回值执行：

| 状态或动作 | 后续操作 |
| --- | --- |
| `process_packets` | 顶层调度动作：查看 `next_packets[]` 中每一项的 `action`，分别执行下列翻译或修复操作 |
| `translate` | 分配尚无结果的包；排除当前仍在运行的 worker 所持任务 |
| `repair` | 生成或继续修复包；有效片段不重译 |
| `repair-apply` | 已有修复结果，运行 `repair --apply` 校验并接收；文件存在不代表其内容已经完整有效 |
| `ready` / `apply` | 所有包通过结构校验，运行 `apply` |
| `applying` / `resume_merge` | 合并中断；`resume` 校验暂存文件与现有源码后完成剩余写入 |
| `applied` / `audit_and_build` | 已合并，继续漏译审查、编译及页面检查 |
| `blocked` / `inspect_inputs` | 检查报告的输入冲突；不覆盖不匹配的源码 |

`.merge/journal.json` 在首次改写源码之前落盘。每个目标文件须与写入前或写入后的哈希匹配，其他输入及结果也须保持一致；任何冲突都在继续写入前停止。已写入的文件不会再次替换。合并完成后允许继续修改排版或修正译文，`resume` 会报告这些变化并引导进入审查、构建；不要再次 `apply` 覆盖它们。

不要删除 `.checks/`、`.repairs/` 或 `.merge/` 来处理失败。`--force` 是显式重建，会移除旧断点与结果；未完成的合并不能直接强制重建。版本 1/2 沿用其已有片段快照，新建版本 3 任务额外记录扫描到的完整 TeX 文件哈希。

若旧任务已用旧版脚本合并、没有 `.merge/journal.json`，脚本无法追溯当时的写入状态。遇到源码快照冲突时，主 agent 检查现有中文源码和交付物，确认已完成合并后继续审查、构建；不要强制重建或再次覆盖源码。

翻译恢复完成后分别运行构建与页面渲染脚本，自动复用哈希匹配的成功产物；详见 [build-and-render.md](build-and-render.md)。恢复命令本身不代替漏译审查、编译或视觉检查。
