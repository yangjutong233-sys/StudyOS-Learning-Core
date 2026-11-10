from datetime import timedelta
import json
from .common import canonical, day, now, require, text, uid
from .schedule import executable_date
from .storage import one, rows


def first_learning(store,knowledge_ids):
    """Earliest recorded real session start or confirmed actual answer, never import time."""
    anchors=[]
    for kid in knowledge_ids:
        candidates=[]
        for r in store.c.execute('SELECT s.id,s.started_at FROM learning_sessions s JOIN lesson_tasks t ON t.id=s.task_id AND t.version=s.task_version WHERE t.knowledge_id=?',(kid,)):
            if not r['started_at']:continue
            actual=day(r['started_at']).isoformat() if len(r['started_at'])==10 else now(r['started_at'])
            candidates.append((actual,'session',r['id']))
        for r in store.c.execute("SELECT a.id,a.occurred_at FROM attempts a JOIN questions q ON q.id=a.question_id AND q.version=a.question_version WHERE q.knowledge_id=? AND json_extract(a.data,'$.time_status')='CONFIRMED'",(kid,)):
            candidates.append((now(r['occurred_at']),'attempt',r['id']))
        if not candidates:return None
        at,kind,identifier=min(candidates)
        anchors.append({'knowledge_id':kid,'date':at[:10],'entity_type':kind,'entity_id':identifier,'actual_at':at})
    require(len({a['date'] for a in anchors})==1,'首次学习日不同，请拆分综合复习','REVIEW_ANCHOR_MISMATCH')
    return {'date':anchors[0]['date'],'evidence':anchors}


def task(store,series,round_number,suggested,extra=None):
    due,unplaced=executable_date(store,store.c,suggested)
    identifier=uid();data={'created_at':store.at,'unplaced':unplaced,**(extra or {})}
    store.c.execute('INSERT INTO review_tasks(id,series_id,round,due_date,suggested_date,data) VALUES(?,?,?,?,?,?)',
                    (identifier,series,round_number,due,suggested,canonical(data)))
    store.event('review_task',identifier,'created',{'series_id':series,'round':round_number,'suggested_date':suggested,'due_date':due,**data})
    return {'id':identifier,'round':round_number,'due_date':due,'suggested_date':suggested,'unplaced':unplaced}


def resolve_anchor(store,t,s,sd,anchor):
    if not sd.get('anchor_pending'):return
    suggested=(day(anchor['date'])+timedelta(days=sd['policy']['intervals'][s['stage']])).isoformat()
    due,unplaced=executable_date(store,store.c,suggested)
    data={**json.loads(t['data']),'anchor_pending':False,'unplaced':unplaced,
          'provisional_suggested_date':t['suggested_date'],'learning_anchor':anchor}
    sd.update({'learning_anchor':anchor,'anchor_pending':False})
    store.c.execute('UPDATE review_series SET data=?,revision=revision+1 WHERE id=?',(canonical(sd),s['id']))
    store.c.execute('UPDATE review_tasks SET suggested_date=?,due_date=?,data=? WHERE id=?',(suggested,due,canonical(data),t['id']))
    store.event('review_series',s['id'],'anchor_confirmed',{'anchor':anchor,'task_id':t['id'],
                'before_provisional_date':t['suggested_date'],'suggested_date':suggested,'due_date':due})
    t.update({'suggested_date':suggested,'due_date':due,'data':canonical(data)})


def create_series(store,raw):
    kind=raw.get('kind');require(kind in ('knowledge','mistake','comprehensive'),'复习类型无效')
    text(raw.get('reason'),'reason');kid=raw.get('knowledge_id');mid=raw.get('mistake_id')
    if kind=='mistake':
        m=one(store.c,'mistakes',mid);kid=m['knowledge_id']
    elif kind=='knowledge': one(store.c,'knowledge_points',kid)
    else:
        members=raw.get('members');require(isinstance(members,list) and members and len(set(members))==len(members),'综合复习需要无重复的知识点集合')
        for member in members: one(store.c,'knowledge_points',member)
        require(kid is None and mid is None,'综合复习使用members')
    policy=store.config('review_policy',store.c)
    members=raw.get('members',[]) if kind=='comprehensive' else [kid]
    subjects={one(store.c,'catalogs',one(store.c,'knowledge_points',k)['catalog_id'])['subject'] for k in members}
    overrides=policy.get('subject_overrides',{})
    selected=[overrides.get(subject,{}) for subject in subjects]
    require(len({canonical(x) for x in selected})==1,'科目复习算法不同，请拆分综合复习','REVIEW_POLICY_MISMATCH')
    policy={**policy,**selected[0]}
    intervals=policy['intervals']
    require(isinstance(intervals,list) and intervals and all(type(x) is int and x>=1 for x in intervals),'复习周期配置无效')
    identifier=raw.get('id') or uid()
    data={**raw,'policy':policy,'created_at':store.at}
    anchored=policy.get('basis')=='first_learning_date'
    anchor=first_learning(store,members) if anchored else None
    if anchored:
        require(raw.get('due_date') is None,'固定D0的原建议日由课件计算；改执行日请用review reschedule')
        data.update({'learning_anchor':anchor,'anchor_pending':anchor is None})
    store.c.execute('INSERT INTO review_series VALUES(?,?,?,?,?,?,?)',(identifier,kind,kid,mid,0,1,canonical(data)))
    store.event('review_series',identifier,'created',data)
    suggested=raw.get('due_date') or (day(anchor['date'] if anchor else store.at[:10])+timedelta(days=intervals[0])).isoformat()
    day(suggested)
    return {'id':identifier,'stage':0,'task':task(store,identifier,1,suggested,
            {'anchor_pending':anchor is None,'unplaced':True} if anchored and anchor is None else None)}


def complete(store,raw):
    t=one(store.c,'review_tasks',raw.get('id'));s=one(store.c,'review_series',t['series_id'])
    require(t['status'] in ('PENDING','ACTIVE'),'复习已经结算或取消','REVIEW_CLOSED')
    require(raw.get('expected_revision')==t['revision'],'复习版本冲突','STALE_REVISION')
    require(not store.c.execute('SELECT 1 FROM review_tasks WHERE series_id=? AND round>?',(s['id'],t['round'])).fetchone(),'旧轮次不能倒序改写新安排','STALE_REVIEW')
    ids=raw.get('assessment_ids') or [raw.get('assessment_id')]
    require(ids and all(isinstance(x,str) for x in ids) and len(set(ids))==len(ids),'需要无重复的独立测试证据')
    sd=json.loads(s['data']);required=set(sd.get('members',[])) if s['kind']=='comprehensive' else {s['knowledge_id']}
    anchored=sd['policy'].get('basis')=='first_learning_date'
    anchor=sd.get('learning_anchor')
    if anchored:
        anchor=anchor or first_learning(store,required)
        require(anchor,'首次学习日尚无真实会话或已确认作答依据，不能以录入日期替代','REVIEW_ANCHOR_REQUIRED')
        require(not raw.get('reset'),'课件固定首次学习日，不允许重置D0；单轮延期请用review reschedule')
        resolve_anchor(store,t,s,sd,anchor)
    evidence=[];covered=set();answer_times=[]
    created=store.c.execute("SELECT event_seq,occurred_at FROM audit_events WHERE entity_type='review_task' AND entity_id=? ORDER BY event_seq LIMIT 1",(t['id'],)).fetchone()
    earliest=max(now(created['occurred_at']),now(t['suggested_date']))
    if anchored:earliest=max(earliest,now((day(anchor['date'])+timedelta(days=sd['policy']['intervals'][s['stage']])).isoformat()))
    for identifier in ids:
        a=one(store.c,'assessments',identifier)
        require(a['status']=='COMPLETED' and json.loads(a['blueprint'])['kind']=='independent','复习需已结算的独立测试','EVIDENCE_INSUFFICIENT')
        require(a['knowledge_id'] in required,'测试不属于复习目标','INVALID_REVIEW')
        seq=store.c.execute("SELECT event_seq,occurred_at FROM audit_events WHERE entity_type='assessment' AND entity_id=? AND event_type='completed'",(identifier,)).fetchone()
        require(seq and seq[0]>created['event_seq'] and now(seq[1])<=store.at,'必须使用本轮创建后完成的测试','EVIDENCE_INSUFFICIENT')
        for previous in store.c.execute("SELECT data FROM review_tasks WHERE result IS NOT NULL"):
            pd=json.loads(previous[0])
            require(identifier not in pd.get('used_assessment_ids',pd.get('assessment_ids',[])),'测试已用于复习评估，请提交新证据','EVIDENCE_REUSED')
        # Recording a historical answer today never makes it current-round evidence.
        completed=store.c.execute("SELECT event_payload FROM audit_events WHERE event_seq=?",(seq[0],)).fetchone()
        attempt_ids=json.loads(completed[0])['attempt_ids']
        require(attempt_ids,'独立测试缺少逐题证据','EVIDENCE_INSUFFICIENT')
        for attempt_id in attempt_ids:
            attempt=one(store.c,'attempts',attempt_id)
            require(attempt['assessment_id']==identifier,'测试证据归属不符','EVIDENCE_INSUFFICIENT')
            require(json.loads(attempt['data']).get('time_status')=='CONFIRMED','复习需要确认的实际作答时间，补录时刻不能替代','EVIDENCE_TIME_UNVERIFIED')
            actual=now(attempt['occurred_at'])
            require(earliest<=actual<=store.at,'实际作答不在本轮验证时间范围内','EVIDENCE_TIME_INVALID',{'earliest':earliest,'actual':actual})
            answer_times.append(actual)
        evidence.append(a['result']);covered.add(a['knowledge_id'])
    require(covered==required,'综合复习未覆盖所有目标','EVIDENCE_INSUFFICIENT')
    if 'UNDETERMINED' in evidence: outcome='UNDETERMINED'
    elif 'FAIL' in evidence: outcome='FAIL'
    elif 'PARTIAL' in evidence: outcome='PARTIAL'
    elif 'HINTED' in evidence: outcome='HINTED'
    else: outcome='PASS'
    policy=sd['policy'];intervals=policy['intervals'];stage=s['stage']
    recovery=json.loads(t['data']).get('recovery',False);streak=json.loads(t['data']).get('recovery_streak',0)
    series_finished=False;next_extra={}
    if anchored:
        if outcome=='UNDETERMINED':gap=None
        elif outcome=='PASS' and (not recovery or streak+1>=policy['recovery_passes']):
            stage+=1;series_finished=stage==len(intervals);gap=None if series_finished else intervals[stage]
        else:
            gap=policy['failure_days'] if outcome=='FAIL' else policy['partial_days']
            next_extra={'recovery':True,'recovery_streak':streak+1 if outcome=='PASS' else 0}
    elif outcome=='PASS': stage=min(stage+1,len(intervals)-1);gap=intervals[stage]
    elif outcome=='FAIL': stage=0;gap=policy.get('failure_days',1)
    elif outcome in ('PARTIAL','HINTED'): gap=max(1,intervals[stage]//2)
    else: gap=None
    default_gap=gap
    override=raw.get('interval_days')
    require(not (series_finished and override is not None),'固定D30轮次已完成；额外训练请另行制定计划')
    require(outcome!='UNDETERMINED' or (override is None and not raw.get('reset')),'无法判定时不能用间隔覆盖代替补证据','EVIDENCE_INSUFFICIENT')
    if override is not None:
        require(type(override) is int and override>=1,'interval_days 必须为正整数');text(raw.get('reason'),'调整理由');gap=override
    if raw.get('reset'):
        text(raw.get('reason'),'重置理由');stage=0;gap=intervals[0]
    previous_data=json.loads(t['data'])
    completed_at=max(answer_times) if outcome!='UNDETERMINED' else None
    data={**previous_data,'assessment_ids':ids,
          'used_assessment_ids':list(dict.fromkeys(previous_data.get('used_assessment_ids',previous_data.get('assessment_ids',[]))+ids)),
          'outcome':outcome,'policy_version':policy['revision'],'evaluated_at':store.at,'actual_completed_at':completed_at,
          'default_gap':default_gap,
          'selected_gap':gap,'reason':raw.get('reason'),
          **({'learning_anchor':anchor,'basis':'first_learning_date','series_finished':series_finished,
              'target_milestone':intervals[s['stage']],'next_recovery':next_extra} if anchored else {})}
    store.c.execute('UPDATE review_tasks SET status=?,completed_at=?,result=?,assessment_id=?,data=?,revision=revision+1 WHERE id=?',
                    ('COMPLETED' if completed_at else 'ACTIVE',completed_at,outcome,ids[0],canonical(data),t['id']))
    store.event('review_task',t['id'],'completed' if completed_at else 'evidence_pending',data,raw['expected_revision'],occurred_at=completed_at)
    if completed_at:
        store.c.execute('UPDATE review_series SET stage=?,revision=revision+1 WHERE id=?',(stage,s['id']))
        store.event('review_series',s['id'],'stage_changed',{'before':s['stage'],'after':stage,'task_id':t['id'],'result':outcome},occurred_at=completed_at)
    next_task=None
    if gap is not None:
        base=anchor['date'] if anchored and not next_extra and override is None else completed_at[:10]
        next_task=task(store,s['id'],t['round']+1,(day(base)+timedelta(days=gap)).isoformat(),next_extra)
    from .evidence_reports import review_report
    report_id=review_report(store,t,s,data,next_task)
    return {'id':t['id'],'result':outcome,'stage':stage,'next':next_task,'revision':t['revision']+1,
            'completed_at':completed_at,'report_artifact_id':report_id}


def adjust(store,raw,cancel=False,start=False):
    t=one(store.c,'review_tasks',raw.get('id'))
    require(t['status'] in ('PENDING','ACTIVE'),'已结算复习不能调整','REVIEW_CLOSED')
    require(raw.get('expected_revision')==t['revision'],'复习版本冲突','STALE_REVISION')
    text(raw.get('reason'),'reason')
    if not cancel:
        s=one(store.c,'review_series',t['series_id']);sd=json.loads(s['data'])
        if sd.get('anchor_pending'):
            members=sd.get('members',[]) if s['kind']=='comprehensive' else [s['knowledge_id']]
            anchor=first_learning(store,members)
            require(anchor,'首次学习日待真实依据，不能启动或正式安排复习','REVIEW_ANCHOR_REQUIRED')
            resolve_anchor(store,t,s,sd,anchor)
    if cancel or start:
        status='CANCELLED' if cancel else 'ACTIVE'
        store.c.execute('UPDATE review_tasks SET status=?,revision=revision+1 WHERE id=?',(status,t['id']))
    else:
        due=day(raw.get('due_date')).isoformat()
        store.c.execute('UPDATE review_tasks SET due_date=?,revision=revision+1 WHERE id=?',(due,t['id']))
    store.event('review_task',t['id'],'cancelled' if cancel else 'started' if start else 'rescheduled',
                {'before_date':t['due_date'],'after_date':raw.get('due_date',t['due_date']),'reason':raw['reason']},raw['expected_revision'])
    return {'id':t['id'],'revision':t['revision']+1}


def reopen(store,raw):
    """Restore a cancelled task as a reminder; it still needs real evidence to complete."""
    t=one(store.c,'review_tasks',raw.get('id'))
    require(t['status']=='CANCELLED','只有已撤销的复习可以恢复','REVIEW_NOT_CANCELLED')
    require(raw.get('expected_revision')==t['revision'],'复习版本冲突','STALE_REVISION')
    reason=text(raw.get('reason'),'reason')
    due=day(raw['due_date']).isoformat() if raw.get('due_date') else t['due_date']
    store.c.execute('UPDATE review_tasks SET status=?,due_date=?,revision=revision+1 WHERE id=?',('PENDING',due,t['id']))
    store.event('review_task',t['id'],'reopened',{'before_status':'CANCELLED','after_status':'PENDING',
                'before_date':t['due_date'],'after_date':due,'reason':reason},raw['expected_revision'])
    return {'id':t['id'],'status':'PENDING','due_date':due,'revision':t['revision']+1}
