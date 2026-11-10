# CLI调用契约

配置 `materials.handoff_scopes` 后，`handoff ensure/export --target both` 只维护对应科目目录各一对标准交接；业务提交后的自动刷新也覆盖这些文件。不生成根目录、408总览、英语编号副本或新的交接归档副本。公开版初始化提供四个系统科目范围、共8份交接；用户可按自己的教材拆分范围，既有历史档案保留。每个范围按 subject、可选 catalog_id/prefix 隔离知识点、任务、复习和证据；撤销的预备任务不作为有效教学任务。directory 是工程内目录，范围不能重叠，文件名固定为CODEX_HANDOFF.md和GPT_TEACHING_HANDOFF.md。尚未配置范围时返回空文件清单，不回退生成根目录文件。所有文件为派生快照，不手工修改，也不将旧交接当学习证据。

系统支持math2、cs408、english1、politics101四个科目键，当前时区固定为Asia/Shanghai。公开版提供通用科目目录，不附带教材、学习历史或个人课表。

日常网页版教学使用对应科目目录内的`GPT_TEACHING_HANDOFF.md`。Codex在保存任务、策略或断点后读取该科目文件，并从handoff show完整元数据或Codex交接核对task_id/task_version再交付；GPT简短隐藏头不直接包含这两个字段。408按具体子科目目录交付。范围内实际ACTIVE/PAUSED会话优先，其次是`lesson prepare`选定的有效任务，最后才回退到最新有效任务；不因新建后续任务而覆盖已选定首课。

`handoff show --id 范围ID --target codex/gpt --json`读取该科目交接并校验新鲜度，返回scope_id和path。不传id时按当前任务定位；无唯一结果时返回HANDOFF_SCOPE_REQUIRED，要求明确范围。`--subject cs408`对应四科，须用`--id`区分。学习进度仍通过summary及相关会话、作答、考核和复习CLI从SQLite查询。

旧备份可能保存408总览或英语编号配置；渲染时只读地选取具体科目、合并等价范围并使用标准文件名，不改写旧配置和学习状态。新配置写入仍拒绝重叠范围或编号副本。

`materials.learning_control.daily_planning=USER_ONLY`启用用户自主学习：GPT交接只呈现实际进度、当前断点和关键未确认项的短摘要，不规划每日内容或上限；完整原答、提示和回传协议由档案窗口按需补充。可以连续学习多个知识点后回传任意格式报告，Codex正式保存原件并按实际内容关联；未知字段留空。只有确实有不同原件内容矛盾时才记录差异待核对，不把缺少JSON或原答当成学习真实性问题，也不以教学评价授予独立掌握。

此模式的GPT正文是简短教学进度摘要，仅列有内容的已学内容、理解情况、未确认之处、已纠正误区、实际断点，不输出教学偏好。不导出导航、材料清单、JSON/CLI协议、内部ID或逐份完整报告。隐藏头只含freshness_key/snapshot_id/content_sha256，handoff show仍可验证新鲜度并返回完整元数据，Codex交接保持原有工程与证据内容。
materials.web_progress_summaries按scope_id保存Codex整理的sections；兼容保留的teaching_preferences不再渲染到GPT交接；sources使用progress_sources返回的record_ids/evaluation_ids/mastery_ids，preference_source保存原教学偏好。来源或偏好改变后不沿用旧摘要，按最新事实渲染；不能把缓存摘要当学习或掌握状态来源。修改需经materials update，禁止手工维护生成文件。
此模式下，非空`plan apply/revise`必须带`source:user_provided`和真实`authorization_ref`，只登记用户明确给出的安排；否则拒绝DAILY_PLANNING_USER_ONLY。空计划修订用于撤销安排，保留历史与已完成项。时间模板、复习规则和考核证据校验继续生效，不自动生成每日学习计划。

统一参数：`--root`、`--input`、`--json`、`--at`。分页`--limit 1..100 --cursor 0..`。
引用内部知识点UUID，外部教材ID在knowledge list/show中返回；不得改写教材ID。

| 操作 | 输入要点 |
|---|---|
| knowledge import | request_id, catalog:{id,subject,name}, points:[{id?,external_id,name,criteria,location?,strong?,weak?}] |
| materials update | request_id, expected_revision, reason, config（完整materials配置）；handoff_scopes[].textbook 可选，用于显示该科目教材名称 |
| materials register | request_id,id,subject,path_key,role,expected_revision（初始0）,bindings:[{knowledge_id,locator}] |
| materials locate/read | --knowledge-id UUID --level 1..4；2层以上需--reason；可用--material-id和--material-version指定历史，或--task及--task-version使用任务锁定版本；PDF只返回物理页定位 |
| lesson create | request_id,id?,knowledge_id,goal,strategy,acceptance,next_task,unfinished_training?,expected_revision（修订时） |
| lesson show | --id 任务ID，分页返回相关会话和学习批次 |
| lesson prepare/start | request_id,task_id；start需材料就绪及强前置满足 |
| lesson pause/finish | request_id,session_id,expected_revision,checkpoint,covered?,minutes?,time_basis（时长非空时） |
| session duration | request_id,session_id,expected_revision,minutes（0–1440）,reason；给已完成会话追加时长更正，原批次保留，摘要按新记录统计 |
| knowledge pending | request_id,id,evidence_report_id,reason；仅把已接受的报告覆盖的LEARNING知识点转为ASSESSMENT_PENDING，不授予掌握 |
| assessment create | request_id,id?,knowledge_id,task_id?,blueprint:{criteria,rationale,min_attempts,forms,dimensions,delay_days?,delay_anchor?} |
| attempt add | request_id,id?,knowledge_id,assessment_id?,session_id?,question_id?,question_version?,question,answer,process,form,dimensions,source,confirmed:true,assistance,evaluation |
| attempt evaluate | request_id,id（原作答）,expected_revision（判定版本）,reason,evaluation |
| assessment submit | request_id,id,expected_revision；从真实逐题证据决定结果 |
| report validate | markdown_path,json_path；不写数据 |
| report import/resolve | request_id,markdown_path,json_path,semantic_review_ref；修订递增版本并给reason；已完成会话首次归档还需supersedes_record_id引用原批次 |
| report receive | request_id,source_path（兼容markdown_path）,knowledge_id?,knowledge_ids?,task_id?,source_ref,reported_date?,reported_summary,reported_facts,user_delivered:true；原格式正式归档，不要求用户提供JSON；知识点已知时登记教学批次，未知时先正式保存待关联，不创建虚构原答或掌握评估。knowledge_ids可列同科目本次覆盖的多个知识点，主点建教学批次；覆盖点从未评估或学习中进入ASSESSMENT_PENDING，已有掌握状态不因普通报告降级；有真实学习日期时分别建立首轮复习，日期未知不建立 |
| report confirm-learning | request_id,id（原报告artifact_id）,user_delivered:true,authorization_ref,knowledge_id?,knowledge_ids?；仅用于先前PENDING或未关联报告，追加正式学习记录或补关联，旧原件保留，重复执行不重复计数 |
| mistake record-event | request_id,id,expected_revision,action,reason,attempt_ids?或resolution_plan |
| review create | request_id,kind,reason；knowledge_id/mistake_id/members依类型指定，due_date? |
| review complete | request_id,id,expected_revision,assessment_id或assessment_ids；interval_days?/reset?需reason |
| review start/reschedule/cancel | request_id,id,expected_revision,reason；延期另给due_date |
| review reopen | request_id,id,expected_revision,reason,due_date?；把已撤销任务恢复为待复习提醒，保留原到期日或指定新日期；不会授予掌握 |
| schedule confirm | request_id,expected_revision,authorization_ref（针对展示草案的真实用户授权） |
| schedule update | request_id,expected_revision,authorization_ref,config（完整新正式配置）；--dry-run可先展示 |
| schedule exception add | request_id,day,authorization_ref,config（当天windows/blocked/max_minutes/rest） |
| policy show/update | name为review_policy/mastery_policy；更新加request_id,expected_revision,reason,config |
| plan validate/apply/revise | id,day,schedule_hash,items:[{id,knowledge_id,start,end,goal,reason,acceptance,review_id?}]；写入加request_id和expected_revision（初始0） |
| plan record-progress | request_id,plan_id,id,expected_revision（计划版本）,expected_item_revision（首次0，后续取返回值）,status,reason |
| audit list/show | --id 实体ID、--entity-type 类型、--request-id 请求ID |
| handoff ensure/export | --target codex/gpt/both；生成器无业务写入、不需request_id |
| backup create/verify | --mode personal/complete；verify加--path |
| restore verify/restore | --path；restore另加--destination，必须是独立新目录 |
| migrate inspect/dry-run | --source 旧根目录；不改变正式数据库 |
| migrate apply | request_id,source,source_fingerprint,backup_path,authorization_ref；不切换数据源 |

`status today / summary / planning-context / knowledge list/show / weaknesses / attempt list / assessment show /
mistake list/show / mastery show/history/evaluate / review due/history / schedule show/validate /
schedule exception list / plan show / report show / doctor / migrate verify`均为查询。
mastery evaluate输出已有证据，不能当作无证据修改掌握的入口。

作答可提供已确认的occurred_at（ISO带时区）；未知时保留空，记录时刻仅作RECORDED_ONLY，不冒称真实答题时刻。
不提供时间时省略occurred_at字段。补录旧答案允许保存，但不能因此作为当前复习证据。
复习证据必须有确认的实际作答时间，且不早于本轮创建时间与最初suggested_date零点两者中的较晚者。
普通独立考核结算同样需要确认的实际作答时间，且不能早于该考核蓝图的创建事件；旧答仍可归档，但不能绑定后建蓝图授予掌握。未知时间可保存无法判定结果，不能改变掌握。
已保存蓝图的迟交考核也不能用较旧作答取代更晚的掌握评估，返回EVIDENCE_STALE；不自动改写已结算历史。
延期不改变该证据起点；结算取本次有效测试证据中最晚的实际作答时间，下次日期从其上海业务日期计算。
时间不明返回EVIDENCE_TIME_UNVERIFIED，时间窗口不符返回EVIDENCE_TIME_INVALID，记录保持原样。

复习UNDETERMINED保存evidence_pending事件和Markdown，本轮保留ACTIVE、completed_at=null，不建下一轮。
使用返回的新revision和新的assessment_id继续补证据；旧证据不得再次使用。
相同request_id重放不重复评估；不能用interval_days或reset跳过待补证据。
assistance：NONE、LIGHT_HINT、HEAVY_HINT、ANSWER_SEEN、UNKNOWN。
evaluation：{result:PASS/PARTIAL/FAIL/UNDETERMINED,basis,reference,error_type?,error_reason?}。
错因：CONCEPT、CALCULATION、MEMORY、READING、UNKNOWN。
独立考核作答source=codex_independent；报告导入强制source=gpt_teaching。
蓝图允许的forms由Codex制定，dimensions必须属于对应科目配置。
delay_days为0..3650整数，默认0；创建时冻结delay_anchor_at与delay_eligible_at（按完整24小时计算）。
默认起点为本次考核创建时间；可指定delay_anchor:{entity_type:assessment或session_record,entity_id:已保存证据ID}，
只能引用同知识点已通过考核或已完成教学记录，不接受任意时间戳。
延迟考核只有在合格时刻后确认作答的题量、不同题目、形式和维度均满足蓝图时才可PASS。
提前通过或时间未确认时返回DELAY_PENDING，考核保持OPEN，不授予掌握；之后追加真实作答，使用新revision提交。
早期作答及各次评估报告完整保留；最终审计列出实际用于结算的证据ID。
升级前OPEN蓝图未保存起点时采用原创建事件；不改写已经结算的历史结论。
纠正action：UNDERSTOOD、GUIDED、ORIGINAL、VARIANT、RESOLVED、REOPEN、PLAN。
resolution_plan：{variant_required:bool,delay_days:非负整数}，改变方案必须说明理由。

计划时间为HH:MM，左闭右开，24:00只可作为日末，跨日拆分。
每周week键0到6代表周一到周日；休息日windows为空。
schedule_hash从planning-context.capacity获得，不能绕过最新配置检查。
待补证据的ACTIVE/UNDETERMINED复习可绑定后续计划；普通正在执行的ACTIVE复习仅能保留已有绑定。已完成项原样保留，不能重新绑定已完成或取消的复习。
同一复习只能绑定最新计划版本中的一个待执行项。跨日改排前，先将原项record-progress为DEFERRED，再在新计划中安排；同计划修订保留原项无需重复绑定。
创建、修订及record-progress恢复ACTIVE共用绑定规则；恢复还校验整份计划的时段、冲突和容量。不能将已取消或已结算复习恢复ACTIVE；已结束复习的原计划项仍可据实记录COMPLETED。
COMPLETED项修订时必须原样保留，作为已发生历史不重新套用新窗口；它仍占用历史时段和已完成分钟。待执行分钟上限=max(0,当前每日上限-已完成分钟)，历史超过新上限时可保存历史，但不能新增待执行分钟。
DEFERRED项不占用时段或容量；重新激活时按最新计划重新验证，不继承延期豁免。caller提供的item.status不能指定数据库中的完成状态。
doctor检查DUPLICATE_REVIEW_BINDING并只报告，不自动更正受影响历史。

## 双格式报告锚点

每个共同字段在MD中出现一次：

```text
<!-- studyos-field:作答ID.answer -->
用户原始作答，不润色
<!-- /studyos-field -->
```

字段包括report_id、version、task_id、session_id、covered、checkpoint、unfinished_training、minutes，
以及每题question、answer、process、assistance、form、dimensions、answer_seen和evaluation的result/basis/reference/error_reason/error_type。
字符串按原文，数组/布尔/空值按JSON字面值；字段外继续保留完整讲解和分析正文。
JSON须有task_version、time_basis（时长存在时）、attempts。
已接受报告修订复用未变化的原答，不重复导入；改变判定需每题expected_evaluation_revision。
原答变化进入待核验。时间修订追加带supersedes_record_id的新批次，原批次不删除，摘要只统计有效批次。
初次报告的minutes表示本报告批次实际时长；若报告对应已记录批次，显式提供supersedes_record_id和reason，禁止重复计时。
代码中的`studyos.reports.markdown_template(data)`生成标准MD骨架，examples提供样例。
自动字段匹配不能代替语义核验，semantic_review_ref必须对应Codex实际核验，不能把任意占位词当真实验证。

## 配置与事件版本

`review_policy.subject_overrides.english1`可使用`basis:"first_learning_date"`和`intervals:[1,3,7,14,30]`；此处数值是相对首次学习日D0的偏移，不是逐轮间隔。其他科目继续使用全局完成日间隔。配置只影响新建系列，已有系列保留冻结的规则。

D0取该知识点系统记录的最早真实学习日期/会话开始时间或已确认实际作答时间，并保存证据ID；已确认的日期级学习报告也可作为D0，但不能推断小时。没有这些依据时，手动创建的复习只保留待确认占位，不能开始或绑定正式计划；补齐依据后用`review reschedule`确认并安排，或在合法的真实复习证据提交时结算。日期未知的报告本身不自动建立系列。占位日期及确认事件保留。不能用导入时间或`reset`重置D0；具体执行日可单独延期。

英语固定里程碑完成后推进至下一个D0偏移；逾期不跳过未完成轮次，休息日只顺延执行日。`failure_days`、`partial_days`决定短间隔补练，`recovery_passes`决定连续独立通过次数；补练不改D0，无法判定保留原轮。D30完成后不自动扩展D60/D90。混合不同算法或不同D0的综合复习需拆分。未确认真实作答时间、旧答重用和考核蓝图等证据校验继续生效。

修改使用当前expected_revision；输出包含before/after。请求键与输入内容绑定，文件导入还绑定输入哈希。
事件的occurred_at是业务时刻，recorded_at为保存时刻；event_seq提供数据库内顺序。
交接文件的source_event_seq不是新学习事件，重建不会增加业务数量。
业务已提交后HANDOFF_FAILED为警告；单独导出失败为错误，原业务不能重复造记录。
输入JSON及已知嵌套字段类型不合法返回VALIDATION_ERROR及退出码2，不写入部分业务。
未预料的内部异常返回INTERNAL_ERROR及退出码4，附系统临时目录诊断路径；此时不能推断业务是否已提交，
先查询审计或用相同request_id重试。诊断只记录异常类型和栈位置，不记录输入原文。

materials默认选择当前知识点每份材料最新的已登记绑定，不遍历旧版本。
文本定位可用`line_ranges:[[起始行,结束行],...]`保存不连续的原始目录或依赖行；范围须递增、不重叠且不越界，读取仍受24000字符限制。原有`line_start/line_end`继续可用。
知识点查询按教材及导入时的`source_order`返回；该顺序只作课件导航。`navigation_only:true`的目录容器不能单独创建教学或考核任务。
历史/任务锁定版本使用登记时的原文件路径，并核对对应哈希；当前版本显式路径重绑定仍按配置生效。
旧登记缺少原路径时只核验配置文件的精确哈希，失败明确报错，不搜索替代文件。
新版本不更改旧任务绑定，也不重置学习状态。完整备份逐版本收录仍可访问且哈希一致的原文件。

## 有界历史和可读证据报告

用户自主模式的GPT交接只输出限长短摘要，完整作答与报告按需由档案窗口补充；以下最近20条明细规则仅用于旧版非自主模式的兼容交接。
列出的错题原答也随文件包含。需要更早证据时，在lesson create中显式给
`context_attempt_ids`（最多20个唯一ID）和`context_reason`；必须属于当前知识点。
文件展示作答总数、所含明细数和扩展理由，不宣称窗口外历史已读或已全部提供。
完整历史不删改；Codex按ID和分页查询后选择具体证据，不自动加载全库。
用户交付的报告按真实学习正式保存，格式不限，JSON由Codex整理。report.receive使用source_path保存原始字节和扩展名；report_policy.user_delivered_is_authentic为true时默认正式接收，也可显式传user_delivered:true。reported_date允许null/省略：已知日期以DATE_ONLY保存，未知以UNKNOWN保存，绝不补零点或使用录入时刻假装学习时间。现有NOT NULL时间列以日期文本或空串表达已知精度，session_records.data保留actual_date/time_precision及空的实际起止时刻；精确延迟起点拒绝日期级或未知时间。首次学习日D0允许使用确认日期，但不推断小时。
用户报告可记初学/部分理解；knowledge状态从未评估或LEARNING进入ASSESSMENT_PENDING（待考核），不授予MASTERED。原报告无逐题答案时不生成attempts或分数；独立掌握由后续测试判断。summary.learning_reports给出正式用户报告数及未关联数。正式接收/确认的报告若有真实学习日期，会自动建立knowledge复习系列（首轮按科目策略，通常1天；未知日期不建立；独立考核结算后已有系列不重复创建）。report.show对二进制原件返回文件路径，文本字段为null；读取格式由Codex选择对应工具，不要求用户转换。materials.teaching_preferences按scope_id保存单次讲解偏好，不作为每日上限。
网页版上传三类材料：本次相关Markdown课件、教材或习题原件的必要页、对应科目目录内的GPT教学交接。用户自主模式的GPT交接不含附件清单；Codex按本科交接的导航及`materials locate/read`查询路径、版本、哈希和必要行号/物理页，再向用户提供所需范围。旧版非自主模式兼容交接才包含“本次Markdown课件（需上传）”与“新窗口教材附件清单”。Markdown按用户选择的实际范围节选并保留原ID，不加载全教材、全历史或无关科目；进度摘要不替代课件附件。

assessment submit、review complete及错题产生/纠正会在records/assessments、records/reviews、
records/mistakes中写不可变Markdown。命令返回report_artifact_id，可用report show读取。
这些文件由已校验的事实、原答、评分和规则编制，不新增语义评分，也不代替GPT完整教学报告。
归档文件与业务记录同一受控写入流程提交，备份会校验并恢复这些原件。

## 精简交接与补录统计

用户自主模式的GPT交接不输出教学偏好；已学内容、学习断点、理解情况、未确认项、误区分别限120/180/140/160/100字符，正文最多约850字符。超长摘录以省略号标记，完整原答和提示由档案窗口按需补充；人工摘要与自动回退使用相同预算。renderer版本变化使旧派生文件自动失效，使用handoff ensure刷新已配置科目的标准交接。

仪表盘证据缺口、累计时长和最近记录均排除被supersedes_record_id替代的批次；多次补录只计最后有效更正。补录分钟不改变time_precision，不补造实际起止时刻；日期缺口保持原数量，未知时长减少。表单选择框及选项统一浅底深字，并显式使用light色彩方案。
