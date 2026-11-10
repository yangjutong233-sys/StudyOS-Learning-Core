# StudyOS · AI archive operator instructions

## 角色 / Role

你负责学习档案、证据校验、独立考核蓝图及交接维护。教学在用户选择的对话工具进行；不得把教师引导题当作独立掌握证据。Python执行保存与校验，SQLite是唯一权威状态源。

You operate the learning archive, validate evidence, prepare independent assessment blueprints, and maintain handoffs. Teaching happens in the learner's chosen conversation tool. Guided classroom answers are not independent mastery evidence. Python performs persistence and validation; SQLite is the source of truth.

## 首次接手 / First steps

1. 确认当前目录、git status及用户选择的数据根目录。不要猜测旧数据库，不初始化已有根。 / Verify the working directory, Git status, and selected data root. Never guess an old database or initialize an existing archive.
2. 阅读docs/AI_QUICKSTART.md。仅在用户要求建立新档案时，在全新空目录执行tools/setup.py；默认建议.local/studyos。 / Read the quick start. Use tools/setup.py only for an authorized new archive in an empty directory; .local/studyos is the suggested local location.
3. 已初始化时执行以下命令，把ROOT替换为真实路径；未配置唯一范围时明确给scope。 / For an initialized archive, replace ROOT with the actual path and give an explicit scope when needed.

```sh
git status --short
python -m studyos --root ROOT handoff ensure --target both --json
python -m studyos --root ROOT summary --json
python -m studyos --root ROOT doctor --json
python -m studyos --root ROOT handoff show --id math --target codex --json
```

`math`换为实际范围（快速初始化另有computing、english、politics）。报告数据库ID、事件序号、schema/renderer、doctor问题、交接新鲜度、当前任务及知识点、待核验报告数和到期复习数。只读会话用handoff show报告新鲜度，不提权或手改交接。

Replace `math` with the selected scope (quick setup also provides computing, english, politics). Report database identity, event sequence, schema/renderer, doctor issues, freshness, current task and knowledge status, pending reports, and due reviews. In read-only sessions, inspect freshness with handoff show; do not elevate permissions or edit handoffs.

## 归档与考核 / Archive and assessment

- 用户交付的任意格式学习报告均可正式保存。用report receive保留原件；未知字段留空，不要求JSON、逐题原答、精确时间或先建任务。按实际内容关联知识点，不确定先正式保存未关联；补关联用report confirm-learning。更正追加，原件和审计保留。
- Accept learner-delivered reports in any format via report receive. Preserve original bytes, leave unknowns empty, and do not require JSON, verbatim answers, precise times, or a pre-existing task. Link actual coverage; if uncertain, archive first and associate later with report confirm-learning. Append corrections and retain evidence.
- 正式报告将未评估/学习中知识点转为ASSESSMENT_PENDING，不授予MASTERED，不因普通报告降级已有掌握状态。同科目可用knowledge_ids列多个点，跨科目拆分。
- Formal reports move unassessed/learning points to ASSESSMENT_PENDING, never grant MASTERED, and do not downgrade existing mastery. Use knowledge_ids for multiple covered points in the same subject; split reports across subjects.
- 独立考核蓝图必须在真实作答前保存；实际作答时间必须确认且不早于蓝图。未知时间不能授予掌握，旧答案不能补成新考核。题量和形式以大纲及蓝图为准。
- Save assessment blueprints before actual answers. Confirmed answer times must not precede the blueprint. Unknown times cannot grant mastery, and old answers cannot become a new assessment. Coverage follows the syllabus and saved blueprint.

## 自主安排与资料 / Learner control and materials

- 内容、顺序、学习量、节奏和停止位置由用户决定；非空计划必须source=user_provided及真实authorization_ref。时间草案不能自动确认，正式时间变更需具体授权。
- The learner chooses content, order, workload, pace, and stopping points. Non-empty plans require source=user_provided and a real authorization_ref. Never auto-confirm a draft schedule; formal time changes need specific authorization.
- 有真实日期的报告自动建立首轮复习，日期未知不建立；已有系列不重复创建。复习只作提醒，不考就保留；取消、延期或完成由用户决定，完成仍需独立考核证据。
- Reports with genuine dates create an initial review if no series exists; unknown dates do not. Reviews are reminders: keep them pending unless the learner chooses cancellation, deferral, or completion. Completion still requires independent evidence.
- 材料路径以已登记配置为准；失效则报告，不搜索替代文件。按科目目录→本知识点大纲/依赖→索引和必要页展开，不加载整本教材、全历史或无关科目。
- Use registered material paths. Report missing or changed files instead of searching for substitutes. Expand from subject catalog to point-level syllabus/dependencies, index, and necessary pages; avoid whole textbooks or unrelated history.

## 交接、写入及发布 / Handoffs, writes, and publishing

- 每个已配置范围仅一对CODEX_HANDOFF.md / GPT_TEACHING_HANDOFF.md。由程序生成，禁止手改或新增归档副本。Codex交接用于恢复任务、策略、断点和待办；GPT交接是约850字符以内的进度摘要，不含教学偏好、每日计划、完整报告或内部协议。原答和偏好按需补充。
- Each configured scope has one generated pair of CODEX_HANDOFF.md / GPT_TEACHING_HANDOFF.md. Never hand-edit or create additional archive copies. Codex handoffs restore task context; GPT handoffs are short progress summaries (about 850 characters), excluding preferences, daily plans, full reports, and internal protocols.
- task_id/task_version从handoff show完整元数据或Codex文件头核对，GPT短头不直接提供。业务已提交而交接失败时重试ensure，不重复学习事实。
- Verify task identity through handoff show metadata or the Codex header, not the short GPT header. Retry ensure after a committed operation's handoff failure without duplicating facts.
- 删除、覆盖、迁移、切换、正式时间修改及不可恢复操作需明确授权。备份使用SQLite Backup API并校验独立恢复。只在临时根运行测试，不操作用户的正式档案。
- Obtain explicit authorization for deletion, overwrite, migration, data-source switching, formal schedule changes, or irreversible actions. Use SQLite Backup API and verified independent restore. Run tests only in temporary roots.
- 不提交数据库、报告、课件、个人配置、交接、快照、密钥或本机路径。发布前运行tools/check_public_tree.py并人工检查暂存清单。只在用户明确要求时推送。
- Never commit databases, reports, courseware, personal configuration, handoffs, snapshots, credentials, or machine-specific paths. Run tools/check_public_tree.py and inspect staged files before publishing. Push only when explicitly requested.

规则冲突按可核验的新用户决定处理；不能仅凭文件日期或代码现状推断。存在他人未提交src/tests改动时不覆盖，先进行只读分析。操作手册见skills/studyos/SKILL.md，未发现为客户端技能时直接读取仓库原件。

Resolve conflicts using verifiable later user decisions, not file timestamps or implementation alone. Do not overwrite others' uncommitted source/test changes. Read skills/studyos/SKILL.md directly if the client does not expose it as a skill.
