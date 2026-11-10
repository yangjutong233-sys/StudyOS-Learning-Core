# AI 快速接手 / AI quick start

此文件面向操作本项目的 AI 助手。根目录 AGENTS.md 为运营规则，docs/CLI.md 为具体命令契约。用户无需手写 JSON。

This guide is for the AI assistant operating StudyOS. AGENTS.md defines operational rules; docs/CLI.md provides the command contract. The learner does not need to author JSON.

## 1. 安装和初始化 / Install and initialize

确认 Python 3.12+ 和项目路径。使用项目隔离环境，避免调用其他安装或其他学习档案。

Verify Python 3.12+ and the repository path. Use this project's isolated environment to avoid another installation or learning archive.

```sh
python -m pip install -e .
python tools/setup.py --root .local/studyos
python -m studyos --root .local/studyos summary --json
python -m studyos --root .local/studyos doctor --json
python -m studyos --root .local/studyos handoff show --id math --target codex --json
```

只对新建空目录运行 setup；已有根直接查询。新初始化没有知识点、课件、学习批次或成绩；交接不代表已学习。默认四个范围：math、computing、english、politics，分别对应math2、cs408、english1、politics101。

Run setup only for a new empty directory; query existing archives directly. A fresh archive has no knowledge points, courseware, learning sessions, or grades. Generated handoffs do not count as study. The four initial scopes are math, computing, english, politics, mapped to math2, cs408, english1, politics101.

## 2. 接入用户资料 / Register the learner's materials

先读用户选择的目录和必要大纲，提取原知识点ID、名称、真实掌握标准和依赖。调用 knowledge import；查看返回的内部UUID，后续关联使用这些ID。没有资料时明确未配置，不搜索作者旧路径、不下载替代教材。

Read the learner-selected catalog and relevant syllabus first. Preserve source IDs, titles, actual criteria, and dependencies. Use knowledge import and retain the returned internal UUIDs for later associations. Without materials, report the gap; do not search for the author's old files or download replacement textbooks.

调用 materials show 读取当前完整配置，再经 materials update 更新本地路径（带expected_revision与真实理由）。使用 materials register 登记角色、哈希、版本及必要行号/物理页。不要直接修改已登记config文件。examples/只展示格式，不得将示例当真实学习导入。

Read the complete current configuration using materials show. Update local paths via materials update with expected_revision and a real reason, then register roles, hashes, versions, and line/page locators using materials register. Never edit registered configuration files directly. examples/ contains format samples, not real learner evidence.

## 3. 接收报告 / Receive reports

读取用户实际交付的原件；提取已讲内容、引导问题、待验证项和实际断点。按真实内容关联同科目知识点；写入report receive的JSON由AI整理，原件使用source_path保留。日期已知只记日期；日期、时刻、时长或原答未知时留空。不确定知识点先保留未关联，之后report confirm-learning。

Read the original report delivered by the learner. Extract covered topics, guided checks, unverified items, and the actual checkpoint. Associate real same-subject coverage; the AI prepares the report receive JSON while preserving the source via source_path. Keep date-only evidence as date-only; leave unknown dates, times, durations, and answers empty. Archive uncertain associations first and use report confirm-learning later.

```sh
python -m studyos --root .local/studyos report receive --input .local/intake.json --json
python -m studyos --root .local/studyos handoff ensure --target both --json
python -m studyos --root .local/studyos summary --json
```

正式接收后知识点是待考核，不能授予MASTERED。重复接收不重复计数。有真实日期时自动建立首轮复习；提醒不等于必须当天考试。

Accepted reports record study and await assessment; they never grant MASTERED. Duplicate intake must not duplicate learning. Real dates create initial review reminders; reminders do not mandate a test that day.

## 4. 考核、时间和复习 / Assessments, schedules, and reviews

用户要求独立考核后，先保存蓝图，再提问并逐题记录原答、提示和确认的真实作答时间。只有符合蓝图及独立性约束的证据才能结算。教学报告的表扬或课堂复述不能替代考核。

When the learner requests independent assessment, save the blueprint first, then ask and record verbatim answers, assistance, and confirmed actual times. Only qualifying evidence may settle the assessment. Classroom praise or guided repetition cannot substitute for assessment.

空课表是未确认草案。报告归档不依赖先配置课表；如用户需要正式时间计划，让其给出可用窗口及具体安排。查询schedule show向其展示；用户确认后用schedule confirm，再经schedule update保存用户提供的完整时间配置（含authorization_ref）。不得把空草案自动确认，也不得替用户填满一天。新系列规则通过policy show/update修改，已有系列保留冻结规则。

The empty schedule is an unconfirmed draft. Report intake does not require a schedule. If the learner wants scheduled plans, obtain their actual available windows and chosen activities. Show schedule show, confirm only after their approval using schedule confirm, then save their full timetable using schedule update with authorization_ref. Never auto-confirm the empty draft or fill the day on their behalf. Use policy show/update for new-series policies; existing series retain frozen policies.

复习未完成就保留，不自动取消。用户要求完成本轮时，需要本轮创建之后、实际建议日期起点之后的合格独立考核证据；取消后可review reopen。

Keep unfinished reviews pending. Completing a round requires qualifying independent evidence after both the round creation time and its original suggested-date boundary. Cancelled reviews can be restored with review reopen.

## 5. 接续与备份 / Continue and back up

每次保存真实业务后核对summary和交接。新会话重新查数据库，不从旧Markdown反推事实。GPT交接正文只输出短进度；材料附件定位由本地档案窗口提供。

After real changes, verify summary and handoffs. New AI sessions query the database instead of inferring state from old Markdown. GPT handoffs contain short progress summaries; the local archive assistant provides material locators separately.

```sh
python -m studyos --root .local/studyos backup create --mode personal --json
python -m studyos --root .local/studyos backup verify --path BACKUP_DIRECTORY --json
```

个人备份不含教材字节，complete模式包含已登记且可验证的教材版本。备份不是Git提交，不向公共仓库上传。

Personal backups exclude textbook bytes; complete backups include registered, verifiable material versions. Backups are not Git commits and must not be uploaded to the public repository.
