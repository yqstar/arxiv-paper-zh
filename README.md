# arxiv-paper-zh

[![npm version](https://img.shields.io/npm/v/arxiv-paper-zh.svg)](https://www.npmjs.com/package/arxiv-paper-zh)
[![license](https://img.shields.io/npm/l/arxiv-paper-zh.svg)](LICENSE)

将 arXiv/LaTeX 论文快速转换为可编译的中文版本。该 Agent Skill 会先核验论文身份，再下载 TeX 源码，按内容量并行翻译，自动检查漏译与 TeX 依赖，最后使用 XeLaTeX 编译并逐页检查 PDF。

方案参考了科学空间的[《让 AI 翻译一篇完整的论文》](https://spaces.ac.cn/archives/11578)，并针对 Codex、Claude Code 等 Agent Skills 兼容环境补充了并行分片、依赖缓存、自动审计和可重复构建流程。

## 快速开始

```bash
npx arxiv-paper-zh@latest install --all
```

重新开启 Agent 会话，然后输入：

```text
$arxiv-paper-zh 翻译 arXiv:2507.15551
```

npm 包地址：[arxiv-paper-zh](https://www.npmjs.com/package/arxiv-paper-zh)。

## 功能

- 核对论文简称、标题、作者、摘要、URL 与 arXiv ID，避免翻错同名论文。
- 按 `arxiv-paper/<paper-name>/` 统一保存原始源码、中文源码以及中英文 PDF。
- 按可见英文词量均衡分片，支持多个 subagent 并行处理长论文。
- 保留公式、数值、引用键、标签、人名、模型名、数据集名和常用缩写。
- 翻译正文、章节标题、脚注、表头、表注和 caption。
- 批量检查并安装缺失的 TeX 宏包，复用共享 TinyTeX/TeX Live 缓存。
- 自动执行漏译审计、BibTeX/Biber 构建和引用收敛检查。
- 使用 XeLaTeX 生成中文 PDF，并要求逐页视觉核验。

## 项目结构

```text
arxiv-paper-zh/
├── .codex-plugin/
│   └── plugin.json
├── .agents/
│   └── plugins/
│       └── marketplace.json
├── bin/
│   └── arxiv-paper-zh.mjs
├── skills/
│   └── arxiv-paper-zh/
│       ├── SKILL.md
│       ├── agents/
│       │   └── openai.yaml
│       ├── scripts/
│       │   ├── audit_tex_translation.py
│       │   ├── build_and_check.py
│       │   ├── inventory_and_shard.py
│       │   ├── prepare_output_layout.py
│       │   └── prepare_tex_runtime.py
│       └── references/
│           └── paper-translation-packages.txt
├── install.sh
├── package.json
├── tests/
│   └── installer.test.mjs
├── .gitignore
└── README.md
```

`skills/arxiv-paper-zh/` 是可以被 Agent 独立加载的完整 Skill；根目录同时提供 Codex Plugin manifest、npm/npx 安装器和原有 Shell 安装器。

## 环境要求

- macOS 或 Linux。
- Python 3.9 或更高版本。
- 可执行 `curl`、`tar` 等常用命令。
- Codex、Claude Code，或其他支持 [Agent Skills 开放格式](https://agentskills.io/) 的客户端。
- 编译时需要 XeLaTeX。可以使用系统 TeX Live，也可以让 Skill 准备并复用 TinyTeX。
- 第一次下载论文源码或安装缺失宏包时需要网络连接。

TeX 运行时不包含在本仓库中，避免让仓库体积增加数百 MB。不同论文缺少的宏包会被批量安装到共享运行时。

## 安装

### 使用 npm / npx（推荐）

无需先克隆仓库，可直接安装到 Codex 和 Claude Code：

```bash
npx arxiv-paper-zh@latest install
```

安装到 Codex、Claude Code 和通用 Agent Skills 目录：

```bash
npx arxiv-paper-zh@latest install --all
```

也可以选择单个客户端或安装到指定项目：

```bash
npx arxiv-paper-zh@latest install --codex
npx arxiv-paper-zh@latest install --claude
npx arxiv-paper-zh@latest install --agents
npx arxiv-paper-zh@latest install --project /path/to/project --all
```

npm 安装器会复制完整 Skill 目录。目标已存在时默认停止；明确需要升级或替换时增加 `--force`。

更新已有的 npm 安装：

```bash
npx arxiv-paper-zh@latest install --all --force
```

### 作为 Codex Plugin 安装

本仓库同时是一个 Codex Plugin，并通过仓库内的 marketplace 从 npm registry 获取版本化包：

```bash
codex plugin marketplace add yqstar/arxiv-paper-zh --ref main
codex plugin add arxiv-paper-zh@arxiv-paper-zh
```

### 从源码一键安装到 Codex 和 Claude Code

```bash
git clone https://github.com/yqstar/arxiv-paper-zh.git
cd arxiv-paper-zh
./install.sh --all
```

安装脚本默认创建符号链接，因此更新仓库后各客户端会立即使用新版本。它不会覆盖已经存在的同名 Skill。

### 仅安装到指定环境

```bash
./install.sh --codex
./install.sh --claude
./install.sh --agents
```

对应的用户级目录为：

| 环境 | 安装位置 |
| --- | --- |
| Codex | `~/.codex/skills/arxiv-paper-zh` |
| Claude Code | `~/.claude/skills/arxiv-paper-zh` |
| 通用 Agent Skills | `~/.agents/skills/arxiv-paper-zh` |

如果客户端已经启动，请重新开启会话；Claude Code 通常支持技能目录的热更新。

### 项目级安装

把 Skill 安装到某个仓库，仅供该项目使用：

```bash
./install.sh --project /path/to/project --agents
./install.sh --project /path/to/project --claude
```

这会分别创建：

- `/path/to/project/.agents/skills/arxiv-paper-zh`
- `/path/to/project/.claude/skills/arxiv-paper-zh`

### 手动安装

也可以直接复制或链接 Skill 子目录。例如：

```bash
ln -s "$(pwd)/skills/arxiv-paper-zh" ~/.codex/skills/arxiv-paper-zh
```

不要只复制 `SKILL.md`，因为工作流还依赖 `scripts/` 和 `references/`。

## 使用

安装后直接向 Agent 描述论文即可：

```text
使用 arxiv-paper-zh 翻译论文 RankMixer。
```

也可以提供更精确的信息：

```text
使用 arxiv-paper-zh 翻译 arXiv:2507.15551。保留公式、引用和人名，
将产物放到 arxiv-paper/RankMixer，分别编译并返回中英文 PDF。
```

Codex 中可显式调用：

```text
$arxiv-paper-zh 翻译 arXiv:2507.15551
```

Claude Code 中可直接输入自然语言，或在发现该 Skill 后使用：

```text
/arxiv-paper-zh 翻译 arXiv:2507.15551
```

如果简称可能对应多篇论文，Skill 会先返回候选论文的完整标题、作者与 arXiv ID，请用户确认后再下载。

安装或更新后若 Skill 没有立即出现，请重新开启 Agent 会话。

## 交付内容

每次任务均按论文简称建立固定目录。例如 `paper-name=EST`：

```text
arxiv-paper/EST/
├── latex/
│   ├── source.tar
│   ├── paper-en/
│   └── paper-zh/
├── paper-en/
│   └── EST-en.pdf
└── paper-zh/
    └── EST-zh.pdf
```

- `latex/source.tar`：从 arXiv 下载的原始压缩包。
- `latex/paper-en/`：未经翻译的英文源码。
- `latex/paper-zh/`：保持可编译性的中文源码副本。
- `paper-en/<paper-name>-en.pdf`：英文原版编译结果。
- `paper-zh/<paper-name>-zh.pdf`：中文译版编译结果。

`paper-name` 使用用户熟悉的简短名称并保留大小写，例如 `EST`、`Onetrans`，且只能包含英文字母、数字、点、下划线和连字符。

参考文献元数据默认不翻译；原图内部文字不修改，只翻译必要 caption。公式内的英文说明按“公式不变”原则保留。

## 内置工具

```bash
# 创建并输出固定的论文产物路径
python3 skills/arxiv-paper-zh/scripts/prepare_output_layout.py EST --root arxiv-paper

# 统计文件并生成均衡翻译分片
python3 skills/arxiv-paper-zh/scripts/inventory_and_shard.py arxiv-paper/EST/latex/paper-zh --entry main.tex --workers 3 --json

# 检查预装包和论文专用宏包
python3 skills/arxiv-paper-zh/scripts/prepare_tex_runtime.py arxiv-paper/EST/latex/paper-zh --preset --kpsewhich /path/to/kpsewhich

# 扫描可能漏译的英文自然语言
python3 skills/arxiv-paper-zh/scripts/audit_tex_translation.py arxiv-paper/EST/latex/paper-zh

# 自动运行 XeLaTeX 与 BibTeX/Biber，检查构建日志
python3 skills/arxiv-paper-zh/scripts/build_and_check.py arxiv-paper/EST/latex/paper-zh/main.tex --tex-bin /path/to/tex/bin
```

## 兼容性说明

本项目遵循以 `SKILL.md` 为入口的 Agent Skills 目录格式。核心翻译与构建步骤可以跨客户端复用，但以下能力取决于具体客户端：

- 是否支持自动触发 Skill。
- 是否支持并行 subagent。
- 是否允许网络下载与安装宏包。
- 是否提供可写文件系统和本地 XeLaTeX。

客户端不支持 subagent 时，Agent 应顺序处理分片；不影响翻译规则与构建脚本的使用。

## 开发与发布检查

```bash
npm run check
npm pack --dry-run

# 可选的独立检查
python3 /path/to/skill-creator/scripts/quick_validate.py skills/arxiv-paper-zh
shellcheck install.sh
```

项目采用 [MIT License](LICENSE)。
