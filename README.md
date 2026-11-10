# StudyOS Learning Core

**把学习交给 AI，把学习事实留在自己手里。**
**Learn with AI. Keep your learning records under your control.**

本地运行的学习档案、独立考核与间隔复习工具。AI 负责理解报告、整理知识点和设计考核；Python 负责校验、保存、审计与备份；SQLite 保存权威状态，Markdown 交接帮助新 AI 会话接续进度。

A local-first learning archive, assessment, and spaced-review toolkit. Your AI assistant interprets reports and prepares assessments; Python validates, stores, audits, and backs up evidence. SQLite is the source of truth, with generated Markdown handoffs for continuity between AI sessions.

本仓库只含程序、通用说明及合成测试。**不含作者的学习数据库、报告、教材、个人配置或历史快照。** 测试中的答案、日期、路径和成绩均为合成数据。

This repository contains software, reusable documentation, and synthetic tests only. **It includes no author's learning database, reports, textbooks, personal configuration, or historical snapshots.** Answers, dates, paths, and grades in tests are synthetic.

## 快速开始 / Quick start

需要 Python 3.12+；运行库仅使用 Python 标准库。建议在隔离环境安装。
Requires Python 3.12+. Runtime code uses only the Python standard library. An isolated environment is recommended.

```sh
git clone https://github.com/yangjutong233-sys/StudyOS-Learning-Core.git
cd StudyOS-Learning-Core
python -m venv .venv
```

激活环境 / Activate the environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```sh
# macOS / Linux
source .venv/bin/activate
```

```sh
python -m pip install -e .
python tools/setup.py --root .local/studyos
python -m studyos --root .local/studyos summary --json
python -m studyos --root .local/studyos doctor --json
python tools/dashboard.py --root .local/studyos --serve
```

`setup.py` 只初始化全新空目录，启用自主学习和任意格式报告，生成科目交接；不会导入示例成绩、选择教材或确认学习时间。初始课表为空草案，真实时间由你提供并确认。再次运行会拒绝覆盖已有内容。

`setup.py` initializes a new, empty directory, enables learner-directed workflows and reports in any format, and generates subject handoffs. It does not import example grades, choose textbooks, or confirm your schedule. The initial schedule is an empty draft. Re-running setup refuses to overwrite existing content.

仪表盘服务只监听本机地址；服务模式可补录已完成会话的时长，静态导出仅供查看。所有持久化学习数据保存在你选择的本地目录。

The dashboard listens on localhost only. Service mode supports duration corrections for completed sessions; a static export is for viewing. Persistent learning data stays in the local directory you select.

## 直接交给 AI / Hand it to your AI

在克隆目录打开能执行本地命令的 AI 编程助手，把下面的话交给它：

Open the cloned repository in an AI coding assistant with local command execution, then send:

> 阅读 AGENTS.md 和 docs/AI_QUICKSTART.md。先检查环境和已有数据库；新项目按快速开始在 .local/studyos 初始化。我会提供自己的课件或学习报告。请保存真实学习事实，不伪造成绩，不替我制定每日学习量。教学在我选择的对话工具进行。

> Read AGENTS.md and docs/AI_QUICKSTART.md. Check the environment and existing database first; initialize a new archive at .local/studyos using the quick start if needed. I will provide my own materials or learning reports. Preserve real learning facts, never invent grades, and let me choose my daily workload. Teaching happens in my chosen conversation tool.

代码不调用模型 API，也不要求 API 密钥。纯网页聊天无法直接操作本机文件，需要你手动上传必要交接、材料页并带回报告。上传给外部 AI 的文件由你选择。

The code makes no model API calls and requires no API key. A browser-only chat cannot operate local files: you manually share the relevant handoff and material excerpts, then bring the report back. You choose which files to share with external AI services.

## 工作方式 / How it works

| 流程 / Step | 行为 / Behavior |
| --- | --- |
| 学习 / Learn | 你决定内容、顺序、学习量；AI 使用你提供的必要材料。 / You choose content, order, and workload; AI uses the material excerpts you provide. |
| 归档 / Archive | 任意格式报告原件先保存；未知日期、时长、原答留空。 / Preserve reports in their original format; leave unknown dates, durations, and verbatim answers empty. |
| 待考核 / Await assessment | 已学不等于掌握，报告不授予 MASTERED。 / Learning reports record study, not independent mastery. |
| 独立考核 / Assess | 先存蓝图，再记录真实独立作答和确认的时间。 / Save a blueprint before recording genuine independent answers and confirmed answer times. |
| 复习 / Review | 自动提醒由你决定是否执行；每轮保留独立历史。 / You decide whether to act on reminders; every round has its own history. |
| 接续 / Continue | 交接由程序生成，新会话先查数据库。 / Handoffs are generated; new sessions query the database first. |
| 备份 / Back up | SQLite Backup API、哈希校验及独立恢复验证。 / SQLite Backup API, hash checks, and independent restore verification. |

## 当前范围 / Current scope

- 系统科目键为 `math2`、`cs408`、`english1`、`politics101`，对应数学、计算机基础、英语、政治；任意自定义顶层科目尚未实现。可在这些科目下登记自己的教材、大纲和知识点。
- Current subject keys are `math2`, `cs408`, `english1`, and `politics101` for mathematics, computer science fundamentals, English, and politics. Arbitrary top-level subjects are not yet supported. Register your own catalogs and knowledge points within these subjects.
- 当前业务时区固定为 `Asia/Shanghai`；跨时区配置尚未实现。
- The business timezone is currently fixed to `Asia/Shanghai`; configurable timezones are not yet supported.
- 默认复习间隔为完成日后 1/3/7/14/30/60/90 天；快速初始化为英语启用首次学习日 D0 的 1/3/7/14/30 偏移。规则按系列冻结，可通过 CLI 配置新系列。
- Default review intervals are 1/3/7/14/30/60/90 days after completion. Quick setup enables English milestones at D0 + 1/3/7/14/30. Each series freezes its policy; CLI updates apply to new series.
- 中文为主要 CLI 与界面语言；本介绍和快速使用说明提供英文。学习档案可靠性约束不能替代 AI 的语义判断。
- CLI and interface text are primarily Chinese; this introduction and quick-start guidance are bilingual. Evidence safeguards do not replace the AI assistant's semantic judgment.

## 文档与开发 / Documentation and development

- [AI 接手规则 / Agent instructions](AGENTS.md)
- [AI 快速接手 / AI quick start](docs/AI_QUICKSTART.md)
- [CLI 契约 / CLI contract](docs/CLI.md)（中文 / Chinese）
- [设计与数据边界 / Architecture and data boundaries](docs/ARCHITECTURE.md)
- [运营技能 / Archive skill](skills/studyos/SKILL.md)
- [贡献说明 / Contributing](CONTRIBUTING.md)
- [隐私与发布检查 / Privacy and release checks](docs/PRIVACY.md)

```sh
python -m unittest discover -s tests -v
python tools/check_public_tree.py
```

测试仅使用临时目录及合成数据；不会读取真实学习数据库。CI 在 Windows、Linux、macOS 上运行测试。

Tests use temporary directories and synthetic data, without reading real learning archives. CI runs the test suite on Windows, Linux, and macOS.

## 许可证 / License

[MIT](LICENSE) · Copyright (c) 2026 yangjutong233-sys.
