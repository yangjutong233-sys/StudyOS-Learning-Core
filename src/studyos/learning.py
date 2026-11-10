import json
from datetime import datetime, timedelta
from .common import SUBJECTS, canonical, day, now, number, require, text, uid
from .storage import one, rows


def latest_task(c,identifier):
    r=c.execute('SELECT * FROM lesson_tasks WHERE id=? ORDER BY version DESC LIMIT 1',(identifier,)).fetchone()
    require(r,'教学任务不存在','NOT_FOUND');return dict(r)


def set_knowledge(store,identifier,status,evidence):
    k=one(store.c,'knowledge_points',identifier)
    if k['status']!=status:
        store.c.execute('UPDATE knowledge_points SET status=?,revision=revision+1 WHERE id=?',(status,identifier))
        store.event('knowledge',identifier,'state_changed',{'before':k['status'],'after':status,'evidence':evidence})


def lesson_create(store,raw):
    k=one(store.c,'knowledge_points',raw.get('knowledge_id'))
    require(not json.loads(k['data']).get('navigation_only'),
            '目录容器只用于导航，请选择其可教学子知识点','NAVIGATION_ONLY')
    for key in ('goal','strategy','acceptance','next_task'): text(raw.get(key),key)
    context_ids=raw.get('context_attempt_ids',[])
    require(isinstance(context_ids,list) and len(context_ids)<=20 and all(isinstance(x,str) for x in context_ids)
            and len(set(context_ids))==len(context_ids),'context_attempt_ids必须为最多20个唯一作答ID')
    if context_ids:
        text(raw.get('context_reason'),'扩大历史读取的 context_reason')
        for attempt_id in context_ids:
            saved=store.c.execute('SELECT q.knowledge_id FROM attempts a JOIN questions q ON q.id=a.question_id AND q.version=a.question_version WHERE a.id=?',(attempt_id,)).fetchone()
            require(saved is not None,'交接证据不存在','NOT_FOUND',attempt_id)
            require(saved['knowledge_id']==k['id'],'不能把其他知识点证据混入当前教学','CONTEXT_SCOPE',attempt_id)
    identifier=raw.get('id') or uid()
    previous=store.c.execute('SELECT MAX(version) FROM lesson_tasks WHERE id=?',(identifier,)).fetchone()[0] or 0
    require(raw.get('expected_revision',0)==previous,'教学任务版本冲突','STALE_REVISION')
    if previous:
        active=store.c.execute("SELECT 1 FROM learning_sessions WHERE task_id=? AND status IN ('ACTIVE','PAUSED')",(identifier,)).fetchone()
        require(not active,'先结算已有教学会话再修改任务版本','SESSION_ACTIVE')
    bindings=rows(store.c,'SELECT km.material_id,km.material_version FROM knowledge_materials km WHERE knowledge_id=? AND material_version=(SELECT MAX(k2.material_version) FROM knowledge_materials k2 WHERE k2.knowledge_id=km.knowledge_id AND k2.material_id=km.material_id)',(k['id'],))
    raw={**raw,'material_bindings':bindings}
    version=previous+1
    store.c.execute('INSERT INTO lesson_tasks VALUES(?,?,?,?,?)',(identifier,version,k['id'],raw['goal'],canonical(raw)))
    store.event('lesson',identifier,'created' if version==1 else 'revised',{'version':version,'task':raw})
    return {'id':identifier,'version':version}


def readiness(store,task):
    links=rows(store.c,'SELECT km.*,m.subject,m.path_key,m.sha256,m.role FROM knowledge_materials km JOIN materials m ON m.id=km.material_id AND m.version=km.material_version WHERE km.knowledge_id=?',(task['knowledge_id'],))
    pinned=json.loads(task['data']).get('material_bindings',[])
    links=[b for b in links if {'material_id':b['material_id'],'material_version':b['material_version']} in pinned]
    require(any(b['role']=='outline' for b in links),'任务版本缺少已绑定大纲，登记后修订任务再开课','MATERIAL_MISSING')
    from .materials import resolve_binding
    from .common import sha
    for b in links:
        p=resolve_binding(store,store.c,b,store.config('materials',store.c))
        require(p.is_file(),'材料不存在','MATERIAL_MISSING',str(p))
        require(sha(p)==b['sha256'],'材料变化需复核','MATERIAL_CHANGED',str(p))


def prepare(store,raw,start=False):
    task=latest_task(store.c,raw.get('task_id'))
    active=store.c.execute("SELECT * FROM learning_sessions WHERE status='ACTIVE'").fetchone()
    require(not active,'必须先暂停或结算活动会话','SESSION_ACTIVE')
    readiness(store,task)
    store.c.execute("INSERT INTO meta VALUES('active_task',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(task['id'],))
    store.event('selection',task['id'],'prepared',{'task_id':task['id'],'version':task['version']})
    if not start: return {'task_id':task['id'],'version':task['version'],'prepared':True}
    blocked=rows(store.c,"SELECT k.id,k.name,k.status FROM knowledge_dependencies d JOIN knowledge_points k ON k.id=d.prerequisite_id WHERE d.knowledge_id=? AND d.kind='strong' AND k.status<>'MASTERED'",(task['knowledge_id'],))
    require(not blocked,'强依赖待诊断或补习','PREREQUISITE_REQUIRED',blocked)
    paused=store.c.execute("SELECT * FROM learning_sessions WHERE task_id=? AND task_version=? AND status='PAUSED' ORDER BY rowid DESC LIMIT 1",(task['id'],task['version'])).fetchone()
    if paused:
        store.c.execute("UPDATE learning_sessions SET status='ACTIVE',revision=revision+1 WHERE id=?",(paused['id'],))
        store.event('session',paused['id'],'resumed',{'task_id':task['id']})
        return {'session_id':paused['id'],'task_id':task['id'],'version':task['version'],'revision':paused['revision']+1,'resumed':True}
    sid=uid()
    store.c.execute('INSERT INTO learning_sessions VALUES(?,?,?,?,?,?)',(sid,task['id'],task['version'],'ACTIVE',store.at,1))
    store.event('session',sid,'started',{'task_id':task['id'],'version':task['version']})
    set_knowledge(store,task['knowledge_id'],'LEARNING',sid)
    return {'session_id':sid,'task_id':task['id'],'version':task['version']}


def settle(store,raw,finish=False,report_append=False):
    s=one(store.c,'learning_sessions',raw.get('session_id'))
    require(s['status'] in ('ACTIVE','PAUSED') or (report_append and s['status']=='COMPLETED'),'会话已完成','SESSION_CLOSED')
    require(raw.get('expected_revision')==s['revision'],'会话版本冲突','STALE_REVISION')
    text(raw.get('checkpoint'),'checkpoint')
    minutes=raw.get('minutes')
    if minutes is not None:
        number(minutes,'minutes');text(raw.get('time_basis'),'time_basis')
    require(isinstance(raw.get('covered',[]),list),'covered 必须为数组')
    supersedes=raw.get('supersedes_record_id')
    if supersedes:
        previous=one(store.c,'session_records',supersedes)
        require(previous['session_id']==s['id'],'不能更正其他会话批次')
        text(raw.get('reason'),'更正实际记录的 reason')
        prior=store.c.execute('SELECT data FROM session_records WHERE session_id=?',(s['id'],)).fetchall()
        require(not any(json.loads(r[0]).get('supersedes_record_id')==supersedes for r in prior),'该记录已经被更正','STALE_REVISION')
    rid=uid();status='COMPLETED' if finish or s['status']=='COMPLETED' else 'PAUSED'
    store.c.execute('INSERT INTO session_records VALUES(?,?,?,?,?,?)',(rid,s['id'],store.at,minutes,raw['checkpoint'],canonical(raw)))
    store.c.execute('UPDATE learning_sessions SET status=?,revision=revision+1 WHERE id=?',(status,s['id']))
    store.event('session',s['id'],'completed' if finish else 'paused',{'record_id':rid,'checkpoint':raw['checkpoint'],'minutes':minutes},raw['expected_revision'])
    task=latest_task(store.c,s['task_id'])
    if finish:
        k=one(store.c,'knowledge_points',task['knowledge_id'])
        if k['status'] not in ('MASTERED','CONSOLIDATE'): set_knowledge(store,k['id'],'ASSESSMENT_PENDING',rid)
    return {'session_id':s['id'],'record_id':rid,'status':status,'revision':s['revision']+1}


def mark_pending_from_report(store,raw):
    """Move a LEARNING point to ASSESSMENT_PENDING using an accepted report as evidence."""
    k=one(store.c,'knowledge_points',raw.get('id'))
    require(k['status']=='LEARNING','只有学习中知识点可以转为待考核','INVALID_STATUS')
    evidence=one(store.c,'artifacts',raw.get('evidence_report_id'))
    require(evidence['kind']=='reports' and evidence['status']=='ACCEPTED','需要已接受的报告作为证据','EVIDENCE_INSUFFICIENT')
    metadata=json.loads(evidence['data'])
    ids=metadata.get('knowledge_ids') or ([metadata['knowledge_id']] if metadata.get('knowledge_id') else [])
    require(k['id'] in ids,'该报告没有覆盖此知识点','INVALID_REVIEW')
    reason=text(raw.get('reason'),'reason')
    set_knowledge(store,k['id'],'ASSESSMENT_PENDING',evidence['id'])
    store.event('knowledge',k['id'],'assessment_pending',{'evidence_report_id':evidence['id'],'reason':reason})
    return {'id':k['id'],'status':'ASSESSMENT_PENDING','evidence_report_id':evidence['id']}


def record_duration(store,raw):
    """Append a user-supplied duration correction without rewriting the original batch."""
    s=one(store.c,'learning_sessions',raw.get('session_id'))
    require(s['status']=='COMPLETED','只能给已完成会话补录时长','SESSION_NOT_COMPLETED')
    require(raw.get('expected_revision')==s['revision'],'会话版本冲突','STALE_REVISION')
    minutes=raw.get('minutes')
    require(type(minutes) in (int,float) and not isinstance(minutes,bool) and 0<minutes<=1440,
            '分钟必须为0到1440之间的数字','VALIDATION_ERROR')
    reason=text(raw.get('reason'),'补录原因')
    old=store.c.execute('SELECT * FROM session_records WHERE session_id=? ORDER BY rowid DESC LIMIT 1',(s['id'],)).fetchone()
    require(old,'会话没有可补录的批次','NOT_FOUND')
    data=json.loads(old['data'])
    precision=data.get('time_precision') or ('DATE_ONLY' if len(old['occurred_at'])==10 else 'EXACT')
    payload={**data,'minutes':minutes,'time_basis':'USER_PROVIDED','time_precision':precision,
             'supersedes_record_id':old['id'],'correction_reason':reason,'duration_source':'USER_MANUAL'}
    rid=uid()
    store.c.execute('INSERT INTO session_records VALUES(?,?,?,?,?,?)',
                    (rid,s['id'],old['occurred_at'],minutes,old['checkpoint'],canonical(payload)))
    store.c.execute('UPDATE learning_sessions SET revision=revision+1 WHERE id=?',(s['id'],))
    store.event('session',s['id'],'duration_recorded',{'record_id':rid,'supersedes_record_id':old['id'],
                'before_minutes':old['minutes'],'minutes':minutes,'reason':reason})
    return {'session_id':s['id'],'record_id':rid,'minutes':minutes,'superseded_record_id':old['id'],
            'revision':s['revision']+1}


def effective_records(records):
    superseded={json.loads(r['data']).get('supersedes_record_id') for r in records}
    return [r for r in records if r['id'] not in superseded]


def create_assessment(store,raw):
    k=one(store.c,'knowledge_points',raw.get('knowledge_id'))
    require(not json.loads(k['data']).get('navigation_only'),
            '目录容器不能单独考核，请选择其可教学子知识点','NAVIGATION_ONLY')
    b=raw.get('blueprint');require(isinstance(b,dict),'需要考核蓝图')
    for key in ('criteria','rationale'): text(b.get(key),key)
    require(type(b.get('min_attempts')) is int and b['min_attempts']>=1,'min_attempts 必须为正整数')
    require(isinstance(b.get('forms'),list) and b['forms'],'必须指定检验形式')
    require(isinstance(b.get('dimensions'),list) and b['dimensions'],'必须指定维度')
    cat=one(store.c,'catalogs',k['catalog_id'])
    allowed=store.config('mastery_policy',store.c)['dimensions'][cat['subject']]
    require(set(b['dimensions'])<=set(allowed),'存在不适用的能力维度')
    delay=b.get('delay_days',0)
    require(type(delay) is int and 0<=delay<=3650,'delay_days 必须为0到3650的整数')
    anchor=store.at;anchor_source={'entity_type':'assessment_created'}
    if 'delay_anchor' in b:
        source=b['delay_anchor'];require(isinstance(source,dict),'delay_anchor 必须为证据引用对象')
        if source.get('entity_type')=='assessment':
            previous=one(store.c,'assessments',source.get('entity_id'))
            require(previous['knowledge_id']==k['id'] and previous['status']=='COMPLETED' and previous['result']=='PASS',
                    '延迟起点必须是同知识点已通过考核','EVIDENCE_INSUFFICIENT')
            event=store.c.execute("SELECT occurred_at FROM audit_events WHERE entity_type='assessment' AND entity_id=? AND event_type='completed'",(previous['id'],)).fetchone()
            require(event is not None,'起点考核缺少完成证据','EVIDENCE_INSUFFICIENT');anchor=now(event[0])
        elif source.get('entity_type')=='session_record':
            record=one(store.c,'session_records',source.get('entity_id'))
            require(len(record['occurred_at'])>10,'该教学记录只有日期或时间未知，不能作为精确延迟计时起点','EVIDENCE_TIME_UNVERIFIED')
            session=one(store.c,'learning_sessions',record['session_id'])
            lesson=store.c.execute('SELECT knowledge_id FROM lesson_tasks WHERE id=? AND version=?',(session['task_id'],session['task_version'])).fetchone()
            event=store.c.execute("SELECT 1 FROM audit_events WHERE entity_type='session' AND entity_id=? AND event_type='completed' AND json_extract(event_payload,'$.record_id')=?",(session['id'],record['id'])).fetchone()
            require(lesson['knowledge_id']==k['id'] and event,'延迟起点必须是同知识点完成教学的记录','EVIDENCE_INSUFFICIENT')
            anchor=now(record['occurred_at'])
        else:
            require(False,'delay_anchor 仅支持assessment或session_record证据')
        require(anchor<=store.at,'延迟起点不能晚于考核创建时刻')
        anchor_source=source
    identifier=raw.get('id') or uid();tid=raw.get('task_id');version=None
    if tid:
        t=latest_task(store.c,tid);version=t['version'];require(t['knowledge_id']==k['id'],'考核不属于该任务')
    b={**b,'delay_days':delay,'delay_anchor_at':anchor,'delay_anchor_source':anchor_source,
       'delay_eligible_at':(datetime.fromisoformat(anchor)+timedelta(days=delay)).isoformat(),
       'outline_criteria':json.loads(k['data'])['criteria'],'policy_version':store.config('mastery_policy',store.c)['revision'],
       'kind':raw.get('kind','independent')}
    require(b['kind'] in ('independent','teaching'),'未知考核类型')
    store.c.execute('INSERT INTO assessments VALUES(?,?,?,?,?,?,?,?)',
                    (identifier,k['id'],tid,version,'OPEN',1,canonical(b),None))
    store.event('assessment',identifier,'created',{'blueprint':b,'knowledge_id':k['id']})
    return {'id':identifier,'revision':1,'blueprint':b}


def add_attempt(store,raw):
    k=one(store.c,'knowledge_points',raw.get('knowledge_id'))
    for key in ('question','answer','process','form','source'): text(raw.get(key),key)
    require(raw.get('confirmed') is True,'需记录已经确认的实际作答')
    attempt_time=now(raw['occurred_at']) if raw.get('occurred_at') else store.at
    require(attempt_time<=store.at,'原始答题时间不能晚于记录业务时刻')
    raw={**raw,'time_status':'CONFIRMED' if raw.get('occurred_at') else 'RECORDED_ONLY'}
    assistance=raw.get('assistance');require(assistance in ('NONE','LIGHT_HINT','HEAVY_HINT','ANSWER_SEEN','UNKNOWN'),'提示等级无效')
    qid=raw.get('question_id') or uid();version=raw.get('question_version',1)
    require(type(version) is int and version>=1,'question_version 无效')
    q=store.c.execute('SELECT * FROM questions WHERE id=? AND version=?',(qid,version)).fetchone()
    if q: require(q['question']==raw['question'] and q['knowledge_id']==k['id'],'题目版本冲突','ID_CONFLICT')
    else: store.c.execute('INSERT INTO questions VALUES(?,?,?,?,?)',(qid,version,k['id'],raw['question'],canonical({'source':raw['source'],'variant_of':raw.get('variant_of')})))
    aid=raw.get('assessment_id')
    if aid:
        assessment=one(store.c,'assessments',aid)
        require(assessment['knowledge_id']==k['id'] and assessment['status']=='OPEN','测试不匹配或已结算','ASSESSMENT_CLOSED')
    sid=raw.get('session_id')
    if sid:
        s=one(store.c,'learning_sessions',sid);t=latest_task(store.c,s['task_id'])
        require(t['knowledge_id']==k['id'],'会话与知识点不符')
    result=raw.get('evaluation',{})
    require(result.get('result') in ('PASS','PARTIAL','FAIL','UNDETERMINED'),'需要逐题判定')
    text(result.get('basis'),'评分依据');text(result.get('reference'),'参考答案或评分标准')
    identifier=raw.get('id') or uid()
    store.c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)',(identifier,qid,version,aid,sid,attempt_time,raw['answer'],assistance,canonical(raw)))
    eval_id=uid()
    store.c.execute('INSERT INTO attempt_evaluations VALUES(?,?,?,?,?,?)',(eval_id,identifier,store.at,1,result['result'],canonical(result)))
    store.event('attempt',identifier,'recorded',{'question_id':qid,'version':version,'evaluation_id':eval_id,'source':raw['source'],'time_status':raw['time_status']},occurred_at=attempt_time)
    store.event('evaluation',eval_id,'created',{'attempt_id':identifier,'result':result})
    mistake=None
    if result['result']=='FAIL' and not raw.get('uncertain'):
        mistake=uid();cause=result.get('error_type','UNKNOWN')
        require(cause in ('CONCEPT','CALCULATION','MEMORY','READING','UNKNOWN'),'错因分类无效')
        md={'created_at':store.at,'resolution_plan':{'variant_required':True,'delay_days':store.config('review_policy',store.c)['mistake_delay_default']},
            'error_reason':result.get('error_reason','待诊断')}
        store.c.execute('INSERT INTO mistakes VALUES(?,?,?,?,?,?,?)',(mistake,identifier,k['id'],'OPEN',1,cause,canonical(md)))
        store.event('mistake',mistake,'created',{'attempt_id':identifier,'cause':cause,'plan':md})
        from .evidence_reports import mistake_report
        mistake_report(store,mistake,md['error_reason'],'created',1)
        from .reviews import create_series
        create_series(store,{'kind':'mistake','mistake_id':mistake,'reason':'实际失败作答后的错题延迟复习'})
    return {'id':identifier,'question_id':qid,'question_version':version,'evaluation_id':eval_id,'mistake_id':mistake}


def submit_assessment(store,raw):
    a=one(store.c,'assessments',raw.get('id'))
    require(a['status']=='OPEN','测试已经结算','ASSESSMENT_CLOSED')
    require(raw.get('expected_revision')==a['revision'],'测试版本冲突','STALE_REVISION')
    b=json.loads(a['blueprint'])
    attempts=rows(store.c,'SELECT a.*,q.knowledge_id FROM attempts a JOIN questions q ON q.id=a.question_id AND q.version=a.question_version WHERE a.assessment_id=?',(a['id'],))
    all_attempts=attempts
    if b['kind']=='independent':
        created=store.c.execute("SELECT occurred_at FROM audit_events WHERE entity_type='assessment' AND entity_id=? AND event_type='created' ORDER BY event_seq LIMIT 1",(a['id'],)).fetchone()
        require(created,'考核缺少预存蓝图的创建证据','EVIDENCE_INSUFFICIENT')
        created_at=datetime.fromisoformat(now(created[0]))
        for item in attempts:
            if json.loads(item['data']).get('time_status')=='CONFIRMED':
                require(created_at<=datetime.fromisoformat(now(item['occurred_at']))<=datetime.fromisoformat(store.at),
                        '独立作答必须发生在蓝图保存之后；原答已保留，请另做新考核','EVIDENCE_TIME_INVALID',
                        {'assessment_id':a['id'],'attempt_id':item['id'],'blueprint_created_at':now(created[0]),'actual':item['occurred_at']})
    delay=b.get('delay_days',0)
    require(type(delay) is int and 0<=delay<=3650,'已保存蓝图delay_days无效，需核验','EVIDENCE_INSUFFICIENT')
    eligible_at=None;delayed=[];delayed_coverage=False
    if delay:
        # Pre-fix OPEN blueprints use their immutable creation event, never today's clock.
        created=store.c.execute("SELECT occurred_at FROM audit_events WHERE entity_type='assessment' AND entity_id=? AND event_type='created' ORDER BY event_seq LIMIT 1",(a['id'],)).fetchone()
        require(created,'考核缺少创建时间，不能推断延迟起点','EVIDENCE_INSUFFICIENT')
        eligible_at=now(b['delay_eligible_at']) if b.get('delay_eligible_at') else (datetime.fromisoformat(now(created[0]))+timedelta(days=delay)).isoformat()
        delayed=[i for i in attempts if json.loads(i['data']).get('time_status')=='CONFIRMED' and eligible_at<=now(i['occurred_at'])<=store.at]
        delayed_coverage=assessment_coverage(store,delayed,b)
        if delayed_coverage: attempts=delayed
    require(len(attempts)>=b['min_attempts'],'考核证据数量不足','EVIDENCE_INSUFFICIENT')
    question_texts={store.c.execute('SELECT question FROM questions WHERE id=? AND version=?',(i['question_id'],i['question_version'])).fetchone()[0] for i in attempts}
    if not b.get('allow_repeated_questions'):
        require(len(question_texts)>=b['min_attempts'],'不同题目数量不足，重复作答不能冒充多道题','EVIDENCE_INSUFFICIENT')
    forms=set();dimensions=set();results=[];independent=True;fresh=False
    for item in attempts:
        d=json.loads(item['data']);forms.add(d['form']);dimensions.update(d.get('dimensions',[]))
        ev=store.c.execute('SELECT * FROM attempt_evaluations WHERE attempt_id=? ORDER BY revision DESC LIMIT 1',(item['id'],)).fetchone()
        results.append(ev['result'])
        independent=independent and item['assistance']=='NONE' and not d.get('answer_seen') and not d.get('uncertain') and d['source']=='codex_independent'
        previous=store.c.execute("SELECT a.data,a.assistance FROM attempts a JOIN questions q ON q.id=a.question_id AND q.version=a.question_version WHERE (a.question_id=? OR q.question=(SELECT question FROM questions WHERE id=? AND version=?)) AND a.id<>? AND a.rowid<(SELECT rowid FROM attempts WHERE id=?)",(item['question_id'],item['question_id'],item['question_version'],item['id'],item['id'])).fetchall()
        exposed=any(p['assistance']!='NONE' or json.loads(p['data']).get('answer_seen') for p in previous)
        if not exposed: fresh=True
        if exposed and not b.get('allow_exposed_retest'): independent=False
    require(set(b['forms'])<=forms and set(b['dimensions'])<=dimensions,'形式或能力维度覆盖不足','EVIDENCE_INSUFFICIENT')
    if 'UNDETERMINED' in results or any(json.loads(i['data']).get('uncertain') for i in attempts): outcome='UNDETERMINED'
    elif 'FAIL' in results: outcome='FAIL'
    elif 'PARTIAL' in results: outcome='PARTIAL'
    elif not independent or (not fresh and not b.get('allow_exposed_retest')): outcome='HINTED'
    else: outcome='PASS'
    if delay and outcome=='PASS' and not delayed_coverage:
        store.c.execute("UPDATE assessments SET result='DELAY_PENDING',revision=revision+1 WHERE id=?",(a['id'],))
        store.event('assessment',a['id'],'delay_pending',{'delay_eligible_at':eligible_at,
            'attempt_ids':[i['id'] for i in all_attempts],'eligible_attempt_ids':[i['id'] for i in delayed]},raw['expected_revision'])
        k=one(store.c,'knowledge_points',a['knowledge_id'])
        if k['status'] not in ('MASTERED','CONSOLIDATE'):set_knowledge(store,k['id'],'ASSESSMENT_PENDING',a['id'])
        from .evidence_reports import assessment_report
        report_id=assessment_report(store,a,{**b,'delay_eligible_at':eligible_at},all_attempts,'DELAY_PENDING')
        return {'id':a['id'],'result':'DELAY_PENDING','status':'OPEN','revision':a['revision']+1,
                'delay_eligible_at':eligible_at,'reason':'延迟后确认作答的题量、形式或维度尚未满足蓝图',
                'report_artifact_id':report_id}
    if b['kind']=='independent' and outcome!='UNDETERMINED':
        require(all(json.loads(i['data']).get('time_status')=='CONFIRMED' for i in attempts),
                '独立结算需确认实际作答时间，录入时刻不能证明蓝图预先保存','EVIDENCE_TIME_UNVERIFIED')
        actual_completed_at=max(datetime.fromisoformat(now(i['occurred_at'])) for i in attempts)
        previous=store.c.execute('SELECT * FROM mastery_assessments WHERE knowledge_id=? ORDER BY rowid DESC LIMIT 1',(a['knowledge_id'],)).fetchone()
        if previous:
            prior=json.loads(previous['data'])
            known=[one(store.c,'attempts',i) for i in prior.get('attempt_ids',[])]
            prior_time=max((datetime.fromisoformat(now(i['occurred_at'])) for i in known
                            if json.loads(i['data']).get('time_status')=='CONFIRMED'),
                           default=datetime.fromisoformat(now(previous['occurred_at'])))
            require(actual_completed_at>=prior_time,'旧作答不能替代更新的掌握评估；请保存历史并进行新复核','EVIDENCE_STALE')
    store.c.execute('UPDATE assessments SET status=?,result=?,revision=revision+1 WHERE id=?',('COMPLETED',outcome,a['id']))
    store.event('assessment',a['id'],'completed',{'result':outcome,'attempt_ids':[i['id'] for i in attempts]},raw['expected_revision'])
    if b['kind']=='independent' and outcome!='UNDETERMINED':
        status='MASTERED' if outcome=='PASS' else 'CONSOLIDATE'
        mid=uid();payload={'dimensions':b['dimensions'],'attempt_ids':[i['id'] for i in attempts],
                         'actual_completed_at':actual_completed_at.isoformat(),
                         'retention':'VERIFIED' if delay and outcome=='PASS' else 'NOT_PASSED' if delay else 'NOT_REQUIRED',
                         'delay_eligible_at':eligible_at,'blueprint':b,'result':outcome}
        store.c.execute('INSERT INTO mastery_assessments VALUES(?,?,?,?,?,?)',(mid,a['knowledge_id'],a['id'],store.at,status,canonical(payload)))
        store.event('mastery',mid,'assessed',{'knowledge_id':a['knowledge_id'],**payload})
        set_knowledge(store,a['knowledge_id'],status,a['id'])
        if not store.c.execute("SELECT 1 FROM review_series WHERE kind='knowledge' AND knowledge_id=?",(a['knowledge_id'],)).fetchone():
            from .reviews import create_series
            create_series(store,{'kind':'knowledge','knowledge_id':a['knowledge_id'],'reason':'独立考核后延迟复习'})
    from .evidence_reports import assessment_report
    report_id=assessment_report(store,a,{**b,'settlement_attempt_ids':[i['id'] for i in attempts]},all_attempts,outcome)
    return {'id':a['id'],'result':outcome,'revision':a['revision']+1,'report_artifact_id':report_id}


def assessment_coverage(store,attempts,blueprint):
    """Only real post-delay evidence may satisfy the frozen blueprint."""
    if len(attempts)<blueprint['min_attempts']:return False
    questions={store.c.execute('SELECT question FROM questions WHERE id=? AND version=?',(i['question_id'],i['question_version'])).fetchone()[0] for i in attempts}
    if not blueprint.get('allow_repeated_questions') and len(questions)<blueprint['min_attempts']:return False
    data=[json.loads(i['data']) for i in attempts]
    return set(blueprint['forms'])<={d['form'] for d in data} and set(blueprint['dimensions'])<={v for d in data for v in d.get('dimensions',[])}


def mistake_event(store,raw):
    m=one(store.c,'mistakes',raw.get('id'))
    require(raw.get('expected_revision')==m['revision'],'错题版本冲突','STALE_REVISION')
    text(raw.get('reason'),'reason');action=raw.get('action')
    require(action in ('UNDERSTOOD','GUIDED','ORIGINAL','VARIANT','RESOLVED','REOPEN','PLAN'),'错题事件类型无效')
    data=json.loads(m['data']);evidence=[]
    if action=='PLAN':
        plan=raw.get('resolution_plan',{})
        require(type(plan.get('variant_required')) is bool and type(plan.get('delay_days')) is int and plan['delay_days']>=0,'纠正方案无效')
        data['resolution_plan']=plan
    if action in ('ORIGINAL','VARIANT','RESOLVED'):
        ids=raw.get('attempt_ids',[]);require(ids,'缺少纠正作答证据','EVIDENCE_INSUFFICIENT')
        original=one(store.c,'attempts',m['attempt_id'])
        for identifier in ids:
            a=one(store.c,'attempts',identifier);q=store.c.execute('SELECT * FROM questions WHERE id=? AND version=?',(a['question_id'],a['question_version'])).fetchone()
            ev=store.c.execute('SELECT result FROM attempt_evaluations WHERE attempt_id=? ORDER BY revision DESC LIMIT 1',(identifier,)).fetchone()
            ad=json.loads(a['data'])
            require(q['knowledge_id']==m['knowledge_id'] and a['assistance']=='NONE' and ev and ev[0]=='PASS' and not ad.get('answer_seen') and not ad.get('uncertain') and ad.get('source')=='codex_independent','纠正证据不足','EVIDENCE_INSUFFICIENT')
            assessment=one(store.c,'assessments',a['assessment_id'])
            require(assessment['status']=='COMPLETED' and assessment['result']=='PASS' and json.loads(assessment['blueprint'])['kind']=='independent','纠正必须有已通过独立考核，不能使用答案暴露后的伪独立证据','EVIDENCE_INSUFFICIENT')
            evidence.append(a)
        variant=any(a['question_id']!=original['question_id'] for a in evidence)
        if action=='VARIANT' or (action=='RESOLVED' and data['resolution_plan']['variant_required']):
            require(variant,'需要新的变式题证据','EVIDENCE_INSUFFICIENT')
        if action=='RESOLVED' and data['resolution_plan']['delay_days']:
            created=day(data['created_at'][:10])
            require(any((day(a['occurred_at'][:10])-created).days>=data['resolution_plan']['delay_days'] for a in evidence),'延迟验证尚未满足','EVIDENCE_INSUFFICIENT')
    status=m['status'] if action=='PLAN' else ('OPEN' if action=='REOPEN' else action)
    store.c.execute('UPDATE mistakes SET status=?,revision=revision+1,data=? WHERE id=?',(status,canonical(data),m['id']))
    store.event('mistake',m['id'],'correction',{'before':m['status'],'after':status,'reason':raw['reason'],
                'attempt_ids':raw.get('attempt_ids',[]),'resolution_plan':data['resolution_plan']},raw['expected_revision'])
    from .evidence_reports import mistake_report
    report_id=mistake_report(store,m['id'],raw['reason'],'correction',m['revision']+1)
    return {'id':m['id'],'status':status,'revision':m['revision']+1,'report_artifact_id':report_id}
