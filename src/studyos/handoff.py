"""Derived, deterministic snapshots. Never consumes an old handoff as evidence."""
import json
from .common import Error, atomic, canonical, digest, now, operation_lock, require, safe_path, sha, uid
from .storage import rows

RENDERER_VERSION = 14
HISTORY_LIMIT = 20
SUMMARY_LIMIT = 10
FILES = {'codex':'CODEX_HANDOFF.md','gpt':'GPT_TEACHING_HANDOFF.md'}


def brief_text(value, limit):
    """Bound derived excerpts; complete evidence stays in the original records."""
    if isinstance(value,list):
        value='；'.join(dict.fromkeys(str(item) for item in value if item))
    value=' '.join(str(value or '').split())
    return value if len(value)<=limit else value[:limit-1]+'…'


def progress_sources(c,scope_ids):
    """Bounded evidence identities keep a curated web summary from going stale."""
    ids=scope_ids or [];marks=','.join('?' for _ in ids) or 'NULL'
    records=rows(c,f"""SELECT r.id,r.occurred_at,r.checkpoint,r.data,k.external_id,k.name
        FROM session_records r JOIN learning_sessions s ON s.id=r.session_id
        JOIN lesson_tasks t ON t.id=s.task_id AND t.version=s.task_version
        JOIN knowledge_points k ON k.id=t.knowledge_id WHERE k.id IN ({marks})
        AND r.id NOT IN (SELECT json_extract(data,'$.supersedes_record_id') FROM session_records
                        WHERE json_extract(data,'$.supersedes_record_id') IS NOT NULL)
        ORDER BY s.rowid DESC,r.rowid DESC LIMIT ?""",(*ids,HISTORY_LIMIT))
    evaluations=rows(c,f"""SELECT e.id FROM attempt_evaluations e JOIN attempts a ON a.id=e.attempt_id
        JOIN questions q ON q.id=a.question_id AND q.version=a.question_version
        WHERE q.knowledge_id IN ({marks}) AND e.revision=(SELECT MAX(revision) FROM attempt_evaluations x WHERE x.attempt_id=e.attempt_id)
        ORDER BY e.rowid DESC LIMIT ?""",(*ids,HISTORY_LIMIT))
    mastery=rows(c,f'SELECT id FROM mastery_assessments WHERE knowledge_id IN ({marks}) ORDER BY rowid DESC LIMIT ?',(*ids,HISTORY_LIMIT))
    sources={'record_ids':[r['id'] for r in records],'evaluation_ids':[r['id'] for r in evaluations],
             'mastery_ids':[r['id'] for r in mastery]}
    return records,sources


def self_directed_gpt(store,c,config,scope_ids,subject,scope_id):
    """A short teaching brief; full evidence and program protocols stay local."""
    records,sources=progress_sources(c,scope_ids)
    preferences=config['materials'].get('teaching_preferences',{}).get(scope_id,[])
    curated=config['materials'].get('web_progress_summaries',{}).get(scope_id,{})
    if curated.get('sources')==sources and curated.get('preference_source')==preferences:
        sections=curated.get('sections',{})
    else:
        # The fallback reads current facts, never old curated text after new evidence.
        sections={};learned=[];understanding=[];gaps=[];errors=[];seen=set()
        for record in records:
            if record['external_id'] in seen:continue
            seen.add(record['external_id'])
            data=json.loads(record['data']);facts=data.get('reported_facts',{})
            learned.append(record['external_id']+' '+record['name'])
            if data.get('reported_summary'):understanding.append(data['reported_summary'])
            gaps.extend(facts.get('unverified',data.get('unfinished_training',[])))
            errors.extend(facts.get('reported_errors',[]))
        if learned:sections['已学内容']=learned
        if understanding:sections['理解情况']=understanding
        if not sources['mastery_ids'] and records:
            sections.setdefault('理解情况',[]).append('尚无独立掌握测试结论。')
        if gaps:sections['尚未确认']=list(dict.fromkeys(gaps))
        if errors:sections['已纠正的误区']=list(dict.fromkeys(errors))
        if records:
            latest=records[0];data=json.loads(latest['data'])
            if latest['checkpoint']:
                sections['学习断点']=latest['checkpoint']
        if sources['mastery_ids']:
            marks=','.join('?' for _ in sources['mastery_ids'])
            results=rows(c,f'''SELECT m.data,k.external_id FROM mastery_assessments m
                JOIN knowledge_points k ON k.id=m.knowledge_id WHERE m.id IN ({marks}) ORDER BY m.rowid DESC''',sources['mastery_ids'])
            seen=set()
            for result in results:
                if result['external_id'] in seen:continue
                seen.add(result['external_id'])
                outcome=json.loads(result['data']).get('result')
                label={'PASS':'已通过独立掌握测试','PARTIAL':'独立测试部分通过，仍需巩固',
                       'FAIL':'独立测试未通过','HINTED':'有提示完成，尚未独立通过',
                       'UNDETERMINED':'独立测试尚无明确结论'}.get(outcome,'独立测试结论见本地记录')
                sections.setdefault('理解情况',[]).append(result['external_id']+'：'+label+'。')
    lines=[]
    if records:
        date=records[0]['occurred_at'][:10]
        if date:lines.append('最近学习：'+date)
    else:lines.append('暂无已登记的学习进度；能力未评估。')
    # A shared budget applies to curated and automatically rebuilt summaries.
    # Show the current stopping point before older details; never export preferences.
    sections=dict(sections)
    understanding=sections.get('理解情况')
    if isinstance(understanding,list):
        sections['理解情况']=[item for item in understanding if item!=sections.get('学习断点')]
    for heading,limit in (('已学内容',120),('学习断点',180),('理解情况',140),
                          ('尚未确认',160),('已纠正的误区',100)):
        value=brief_text(sections.get(heading),limit)
        if not value:continue
        lines+=['## '+heading,value]
    if records:lines+=['以上为摘要；原答、提示和完整报告由档案窗口按需补充。']
    lines+=['学习内容、顺序和学习量由用户决定。']
    return '\n\n'.join(lines)+'\n'


def textbook(k,config):
    """Resolve the textbook label for the scope that owns this point."""
    for scope in config.get('handoff_scopes',[]):
        if scope.get('subject')!=k['subject']:
            continue
        catalog=scope.get('catalog_id')
        if catalog and catalog!=k['catalog_id']:
            continue
        prefix=scope.get('prefix') or ''
        if prefix and not k['external_id'].startswith(prefix):
            continue
        if scope.get('textbook'):
            return scope['textbook']
    return k.get('catalog_name') or ''


def context(store,c):
    config={n:store.config(n,c) for n in ('schedule','materials','review_policy','mastery_policy')}
    meta={r['key']:r['value'] for r in c.execute('SELECT * FROM meta')}
    seq=c.execute('SELECT COALESCE(MAX(event_seq),0) FROM audit_events').fetchone()[0]
    task=None; knowledge=None
    if meta.get('active_task'):
        r=c.execute('SELECT * FROM lesson_tasks WHERE id=? ORDER BY version DESC LIMIT 1',(meta['active_task'],)).fetchone()
        if r:
            task=dict(r); task['data']=json.loads(task['data'])
            knowledge=dict(c.execute('SELECT k.*,c.subject,c.name AS catalog_name FROM knowledge_points k JOIN catalogs c ON c.id=k.catalog_id WHERE k.id=?',(task['knowledge_id'],)).fetchone())
            knowledge['data']=json.loads(knowledge['data'])
    return {'database_id':meta['database_id'],'database_schema_version':1,'source_event_seq':seq,
            'renderer_version':RENDERER_VERSION,'business_date':store.at[:10],'task_id':task['id'] if task else None,
            'task_version':task['version'] if task else None,
            'config_hashes':{n:digest(canonical(v).encode()) for n,v in config.items()}},task,knowledge,config


def current(path,expected):
    try:
        header,body=path.read_text(encoding='utf-8').split('\n',1)
        require(header.startswith('<!-- studyos ') and header.endswith(' -->'),'快照头无效')
        old=json.loads(header[13:-4])
        metadata_ok=(old.get('freshness_key')==digest(canonical(expected).encode()) if 'freshness_key' in old
                     else all(old.get(k)==v for k,v in expected.items()))
        return metadata_ok and old.get('content_sha256')==digest(body.encode()),old
    except (OSError,ValueError,Error):
        return False,{}


def bodies(store,c,meta,task,k,config,scope_ids=None):
    self_directed=config['materials'].get('learning_control',{}).get('daily_planning')=='USER_ONLY'
    selected=task['id'] if task else None
    pending=rows(c,"SELECT id,entity_id,path,data FROM artifacts WHERE kind='reports' AND status='PENDING' ORDER BY rowid DESC LIMIT ?",(SUMMARY_LIMIT,))
    pending_count=c.execute("SELECT COUNT(*) FROM artifacts WHERE kind='reports' AND status='PENDING'").fetchone()[0]
    due=rows(c,"SELECT id,series_id,due_date,status FROM review_tasks WHERE status IN ('PENDING','ACTIVE') AND due_date<=? ORDER BY due_date LIMIT ?",(store.at[:10],SUMMARY_LIMIT))
    due_count=c.execute("SELECT COUNT(*) FROM review_tasks WHERE status IN ('PENDING','ACTIVE') AND due_date<=?",(store.at[:10],)).fetchone()[0]
    if scope_ids is not None:
        marks=','.join('?' for _ in scope_ids) or 'NULL'
        report_filter=f"kind='reports' AND status='PENDING' AND (entity_id IN (SELECT id FROM lesson_tasks WHERE knowledge_id IN ({marks})) OR json_extract(data,'$.knowledge_id') IN ({marks}))"
        pending=rows(c,f'SELECT id,entity_id,path,data FROM artifacts WHERE {report_filter} ORDER BY rowid DESC LIMIT ?',(*scope_ids,*scope_ids,SUMMARY_LIMIT))
        pending_count=c.execute(f'SELECT COUNT(*) FROM artifacts WHERE {report_filter}',(*scope_ids,*scope_ids)).fetchone()[0]
        review_filter=f"status IN ('PENDING','ACTIVE') AND due_date<=? AND series_id IN (SELECT id FROM review_series WHERE knowledge_id IN ({marks}) OR EXISTS (SELECT 1 FROM json_each(review_series.data,'$.members') WHERE value IN ({marks})))"
        args=(store.at[:10],*scope_ids,*scope_ids)
        due=rows(c,f'SELECT id,series_id,due_date,status FROM review_tasks WHERE {review_filter} ORDER BY due_date LIMIT ?',(*args,SUMMARY_LIMIT))
        due_count=c.execute(f'SELECT COUNT(*) FROM review_tasks WHERE {review_filter}',args).fetchone()[0]
    codex=['# Codex 最新工作交接','由程序生成；修改业务数据后重建，禁止手工维护。',
           f"数据源：{store.root}",f"时间配置：{config['schedule']['status']}（未确认不可正式排计划）",
           '## 当前状态',canonical({'task_id':selected,'knowledge':k and {x:k[x] for x in ('id','external_id','name','subject','status')}}),
           '## 待核验报告',canonical({'total':pending_count,'items':pending,'more':pending_count>len(pending)}),
           '## 到期复习摘要',canonical({'total':due_count,'items':due,'more':due_count>len(due)}),
           '## 恢复操作','先核对数据库，再按任务展开资料；不读取全教材或全历史。',
           '```powershell',f'studyos --root "{store.root}" handoff ensure --target both --json',f'studyos --root "{store.root}" planning-context --json' ,'```',
           '## 工程状态','软件设计与使用见源码仓库 docs/ARCHITECTURE.md 和 docs/AI_QUICKSTART.md；合成测试不代表真实学习。']
    gpt=['# GPT 网页教学交接','由程序生成。本文件是当前教学快照，不需要访问本地数据库或运行本机命令。']
    guidance=config['materials'].get('curriculum_guidance')
    if guidance:
        codex+=['## 课件执行约定',guidance]
        gpt+=['## 课件执行约定',guidance]
    preferences=config['materials'].get('teaching_preferences',{}).get(meta.get('scope_id'),[])
    if preferences:codex+=['## 教学交互偏好',canonical(preferences)]
    if not task:
        gpt+=['尚未选定教学任务；请先由用户选择科目和知识点，不自动开课。']
        return '\n\n'.join(codex)+'\n',self_directed_gpt(store,c,config,scope_ids,meta.get('subject'),meta.get('scope_id')) if self_directed else '\n\n'.join(gpt)+'\n'
    d=task['data']; kd=k['data']
    sessions=rows(c,"""SELECT r.* FROM session_records r JOIN learning_sessions s ON s.id=r.session_id
        WHERE s.task_id=? AND r.id NOT IN
        (SELECT json_extract(data,'$.supersedes_record_id') FROM session_records
         WHERE json_extract(data,'$.supersedes_record_id') IS NOT NULL)
        ORDER BY r.rowid DESC LIMIT ?""",(selected,HISTORY_LIMIT))
    sessions.reverse()
    attempt_sql='SELECT a.id,a.answer,a.assistance,a.occurred_at,a.rowid AS position,q.question,a.data FROM attempts a JOIN questions q ON q.id=a.question_id AND q.version=a.question_version'
    attempts=rows(c,attempt_sql+' WHERE q.knowledge_id=? ORDER BY a.rowid DESC LIMIT ?',(k['id'],HISTORY_LIMIT))
    attempt_total=c.execute('SELECT COUNT(*) FROM attempts a JOIN questions q ON q.id=a.question_id AND q.version=a.question_version WHERE q.knowledge_id=?',(k['id'],)).fetchone()[0]
    mistakes=rows(c,'SELECT id,attempt_id,status,cause,data FROM mistakes WHERE knowledge_id=? ORDER BY rowid DESC LIMIT ?',(k['id'],HISTORY_LIMIT))
    # Explicit old evidence and displayed mistakes remain self-contained even outside the recent window.
    additional_ids=list(dict.fromkeys(d.get('context_attempt_ids',[])+[m['attempt_id'] for m in mistakes]))
    by_id={a['id']:a for a in attempts}
    for attempt_id in additional_ids:
        if attempt_id not in by_id:
            extra=rows(c,attempt_sql+' WHERE a.id=? AND q.knowledge_id=?',(attempt_id,k['id']))
            require(bool(extra),'交接指定证据不存在或归属变化','CONTEXT_SCOPE',attempt_id)
            by_id[attempt_id]=extra[0]
    attempts=sorted(by_id.values(),key=lambda a:a['position'])
    exercise=[]
    for a in attempts:
        ev=c.execute('SELECT result,data FROM attempt_evaluations WHERE attempt_id=? ORDER BY revision DESC LIMIT 1',(a['id'],)).fetchone()
        ad=json.loads(a['data'])
        exercise.append({'证据ID':a['id'],'题目':a['question'],'原始作答':a['answer'],'提示':a['assistance'],
                         '过程':ad.get('process','未记录'),'结果':ev['result'] if ev else '待核验',
                         '错误原因':json.loads(ev['data']).get('error_reason','未确认') if ev else '未确认'})
    mastery=rows(c,'SELECT occurred_at,status,data FROM mastery_assessments WHERE knowledge_id=? ORDER BY rowid DESC LIMIT ?',(k['id'],HISTORY_LIMIT))
    mastery.reverse()
    task_pending=rows(c,"SELECT id,path,data FROM artifacts WHERE kind='reports' AND status='PENDING' AND entity_id=? ORDER BY rowid DESC LIMIT ?",(selected,SUMMARY_LIMIT))
    bindings=rows(c,'SELECT km.*,m.subject,m.path_key,m.sha256,m.role,m.data AS material_data FROM knowledge_materials km JOIN materials m ON m.id=km.material_id AND m.version=km.material_version WHERE km.knowledge_id=?',(k['id'],))
    attachments=[]; navigation=[]
    from .materials import resolve_binding
    for b in bindings:
        if {'material_id':b['material_id'],'material_version':b['material_version']} not in d.get('material_bindings',[]): continue
        path=resolve_binding(store,c,b,config['materials'])
        status='需用户上传'
        if not path.is_file(): status='缺失：请用户提供，不寻找替代文件'
        elif sha(path)!=b['sha256']: status='版本已变化：需核验，不能沿用'
        # Solutions are not teaching attachments by default; never expose answer sheets.
        if b['role'] in ('answer','reserved_answer'): continue
        if b['role'] in ('catalog','outline','dependencies','index'):
            navigation.append({'文件名':path.name,'用户查找路径':str(path),'版本':b['material_version'],
                               'sha256':b['sha256'],'定位':json.loads(b['locator']),
                               '角色':b['role'],'状态':status,'上传要求':'需上传本次定位范围的Markdown课件，保留原ID、版本及必要依赖；不展开无关内容'})
            continue
        attachments.append({'文件名':path.name,'用户查找路径':str(path),'版本':b['material_version'],
                            'sha256':b['sha256'],'定位':json.loads(b['locator']),'用途':b['role'],'状态':status})
    latest_record=json.loads(sessions[-1]['data']) if sessions else {}
    content={'科目':k['subject'],'知识点ID':k['external_id'],'名称':k['name'],'教材':textbook(k,config),
             '教材定位':kd.get('location','待补充'),'学习目标':task['goal'],'掌握标准':kd.get('criteria','待提供'),
             '当前状态':k['status'],'当前教学策略':d.get('strategy','未保存，需Codex补充'),
             '已讲解内容':[json.loads(r['data']).get('covered',[]) for r in sessions],
             '教学断点':[{'checkpoint':r['checkpoint'],'date':r['occurred_at']} for r in sessions],
             '已完成练习':exercise,'原始错误及原因':mistakes,'已掌握与未掌握':mastery or '尚无正式考核证据',
             '尚未完成强化训练':latest_record.get('unfinished_training',d.get('unfinished_training',[])),
             '下一步教学任务':latest_record.get('next_task',d.get('next_task',task['goal'])),'完成标准':d.get('acceptance',kd.get('criteria')),
             '本次Markdown课件（需上传）':navigation,
             '新窗口教材附件清单':attachments,'待核验报告':task_pending,
             '历史范围':{'知识点作答总数':attempt_total,'本文件作答明细数':len(exercise),'最近明细窗口':HISTORY_LIMIT,
                         '显式补充证据':d.get('context_attempt_ids',[]),'扩大历史原因':d.get('context_reason'),
                         '说明':'完整历史保留在原档案；本文件包含当前教学所需的最近记录、已列错题原答及明确指定证据。需要其他历史时请Codex按证据定位补充，不让网页访问本机。'}}
    for title,value in content.items():
        gpt+=['## '+title, value if isinstance(value,str) else canonical(value)]
    gpt+=['## 教学边界与成果报告',
          '先核对用户已上传附件；未上传不声称已读。只讲当前任务和必要前置。保留真实原答、提示和训练过程。',
          '输出完整Markdown成果报告和配套JSON，不让用户手写JSON。每题使用相同题目ID及Markdown锚点；不一致交回Codex待核验。',
          '网页评价仅为教学证据，不代替Codex独立考核。不要提前提供独立考核参考答案。']
    last_session=c.execute('SELECT id FROM learning_sessions WHERE task_id=? ORDER BY rowid DESC LIMIT 1',(task['id'],)).fetchone()
    report_shape={'report_id':'report1_REPLACE_WITH_UNIQUE_ID','version':1,'task_id':task['id'],'task_version':task['version'],
                  'session_id':last_session[0] if last_session else None,'covered':[],
                  'checkpoint':'实际教学断点','unfinished_training':[],'minutes':None,'time_basis':None,
                  'attempts':[{'id':'report1_REPLACE_WITH_UNIQUE_ID_q1','question':'题目原文','answer':'用户原始作答','process':'用户原始过程',
                      'form':'application','dimensions':[],'assistance':'UNKNOWN','answer_seen':None,
                      'evaluation':{'result':'UNDETERMINED','basis':'实际评分依据','reference':'标准答案或评分标准',
                                    'error_type':'UNKNOWN','error_reason':'实际原因或待诊断'}}]}
    gpt+=['## 可直接使用的报告数据契约','```json\n'+json.dumps(report_shape,ensure_ascii=False,indent=2)+'\n```',
          '填入实际发生内容，删除示例作答并逐题追加。时长未知保持null；只记录用户确认的有效分钟。session_id为null时由Codex归档前绑定真实会话，不猜测。',
          'assistance使用NONE/LIGHT_HINT/HEAVY_HINT/ANSWER_SEEN/UNKNOWN；判定使用PASS/PARTIAL/FAIL/UNDETERMINED；错因使用CONCEPT/CALCULATION/MEMORY/READING/UNKNOWN。',
          '适用能力维度：'+canonical(config['mastery_policy']['dimensions'][k['subject']]),
          'MD保留完整讲解和分析，共同字段使用下面的锚点格式。每个字段仅出现一次，原答不润色。',
          '```text\n<!-- studyos-field:report1_q1.answer -->\n用户原始作答\n<!-- /studyos-field -->\n```',
          '报告级字段：report_id、version、task_id、session_id、covered、checkpoint、unfinished_training、minutes。'+
          '逐题字段：作答ID.question/answer/process/assistance/form/dimensions/answer_seen及作答ID.evaluation.result/basis/reference/error_reason/error_type。'+
          '字符串保持原文，数组、布尔和null使用JSON字面值。作答ID使用英文字母数字短横线下划线，保证全局唯一，同时出现在MD和JSON。']
    codex+=['## 当前任务策略与断点',canonical({'strategy':d.get('strategy'),'next_task':d.get('next_task'),
              'checkpoint':sessions[-1]['checkpoint'] if sessions else None}),
            '## 资料分支','当前科目总目录 → '+k['external_id']+' 大纲和依赖 → 必要索引/PDF页。扩大范围必须说明原因。']
    return '\n\n'.join(codex)+'\n',self_directed_gpt(store,c,config,scope_ids,meta.get('subject'),meta.get('scope_id')) if self_directed else '\n\n'.join(gpt)+'\n'


def render(store,c,target='both',ensure=False):
    require(target in ('both','codex','gpt'),'未知交接目标')
    c.execute('BEGIN')
    try:
        meta,task,k,config=context(store,c)
        from .subject_handoffs import documents
        scoped=documents(store,c,meta,config)
        targets=list(FILES) if target=='both' else [target]
        fresh=True
        snapshots=set()
        for group in scoped:
            states=[current(group['directory']/group['files'][t],group['metadata']) for t in targets]
            fresh=fresh and all(v[0] for v in states)
            snapshots.update(v[1].get('snapshot_id') for v in states)
        if target=='both' and scoped:
            fresh=fresh and len(snapshots)==1
        files=[str(g['directory']/g['files'][t]) for g in scoped for t in targets]
        if ensure and fresh:
            return {'regenerated':False,'metadata':meta,'files':files}
        snapshot=uid()
        for group in scoped:
            for t in targets:
                body=group[t]
                header={**group['metadata'],'snapshot_id':snapshot,'generated_at':now(),'content_sha256':digest(body.encode())}
                if t=='gpt' and config['materials'].get('learning_control',{}).get('daily_planning')=='USER_ONLY':
                    header={'freshness_key':digest(canonical(group['metadata']).encode()),
                            'snapshot_id':snapshot,'content_sha256':digest(body.encode())}
                document='<!-- studyos '+canonical(header)+' -->\n'+body
                atomic(group['directory']/group['files'][t],document)
        return {'regenerated':bool(scoped),'metadata':{**meta,'snapshot_id':snapshot},'files':files}
    finally:
        c.rollback()  # ends read snapshot; no business writes


def show(store,c,raw):
    from .subject_handoffs import documents
    meta,task,k,config=context(store,c)
    target=raw.get('target','codex')
    require(target in FILES,'show需指定codex或gpt')
    groups=documents(store,c,meta,config)
    if raw.get('id'):
        groups=[g for g in groups if g['id']==raw['id']]
    elif raw.get('subject'):
        groups=[g for g in groups if g['metadata']['subject']==raw['subject']]
    elif task:
        groups=[g for g in groups if g['metadata']['task_id']==task['id']]
    require(len(groups)==1,'请用 --id 指定唯一科目交接范围','HANDOFF_SCOPE_REQUIRED',
            {'scope_ids':[g['id'] for g in groups]})
    group=groups[0];p=group['directory']/group['files'][target]
    fresh,header=current(p,group['metadata'])
    if fresh and 'freshness_key' in header:header={**group['metadata'],**header}
    return {'scope_id':group['id'],'path':str(p),'fresh':fresh,'metadata':header,
            'text':p.read_text(encoding='utf-8') if p.exists() else None,
            'repair':'studyos handoff ensure --target both'}


def handoff(store,target='both',ensure=False):
    require(store.path.is_file(),'数据库未初始化','NOT_INITIALIZED')
    store.guard()
    with operation_lock(store.root):
        with store.connect(True) as c:
            return render(store,c,target,ensure)
