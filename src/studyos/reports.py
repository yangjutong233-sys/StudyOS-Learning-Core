"""Human-readable report plus verifiable field anchors. Semantic review is explicit."""
import json
import re
from .common import canonical, day, digest, load, require, text, uid
from .storage import one


def report_points(store,raw,point):
    """Resolve the primary point plus optional additional covered points."""
    ids=raw.get('knowledge_ids')
    if ids is None:
        return ([point['id']] if point else []),point
    require(isinstance(ids,list) and 1<=len(ids)<=20 and all(isinstance(x,str) for x in ids),
            'knowledge_ids必须为1到20个知识点ID','VALIDATION_ERROR')
    ids=list(dict.fromkeys(ids))
    points=[one(store.c,'knowledge_points',identifier) for identifier in ids]
    subjects={one(store.c,'catalogs',item['catalog_id'])['subject'] for item in points}
    require(len(subjects)==1,'一份学习报告只能关联同一科目的知识点；跨科目请拆分','REPORT_CONFLICT')
    if point is None:point=points[0]
    require(point['id'] in ids,'主知识点必须包含在knowledge_ids中','REPORT_CONFLICT')
    return ids,point


def ensure_first_review(store,identifier,date):
    """Schedule the first knowledge review for one point, never duplicating a series."""
    point=one(store.c,'knowledge_points',identifier)
    if not date or point['status'] in ('MASTERED','CONSOLIDATE'):
        return None
    exists=store.c.execute("SELECT 1 FROM review_series WHERE kind='knowledge' AND knowledge_id=?",(identifier,)).fetchone()
    if exists:return None
    from datetime import timedelta
    from .reviews import create_series
    subject=one(store.c,'catalogs',point['catalog_id'])['subject']
    policy=store.config('review_policy',store.c)
    override=policy.get('subject_overrides',{}).get(subject,{})
    anchored=(override or policy).get('basis')=='first_learning_date'
    request={'kind':'knowledge','knowledge_id':identifier,'reason':'叙述学习记录后自动建立首轮复习'}
    if not anchored:
        intervals=override.get('intervals') or policy['intervals']
        request['due_date']=(day(date)+timedelta(days=intervals[0])).isoformat()
    created=create_series(store,request)
    return {'knowledge_id':identifier,'series_id':created['id'],'task_id':created['task']['id'],'due_date':created['task']['due_date']}


def receive_report(store,raw):
    """Receive user-delivered reports in their original format, without inventing answers."""
    from pathlib import Path
    point=one(store.c,'knowledge_points',raw['knowledge_id']) if raw.get('knowledge_id') else None
    source_path=Path(raw.get('source_path') or raw['markdown_path'])
    source=source_path.read_bytes()
    require(source.strip(),'教学报告不能为空')
    source_hash=raw.get('source_sha256') or raw.get('markdown_sha256')
    require(digest(source)==source_hash,'报告在接收期间变化','REPORT_CHANGED')
    text(raw.get('source_ref'),'报告来源source_ref')
    text(raw.get('reported_summary'),'原报告结论reported_summary')
    facts=raw.get('reported_facts',{})
    require(isinstance(facts,dict),'reported_facts必须为对象')
    for key in ('covered','guided_checks','unverified','reported_errors'):
        require(isinstance(facts.get(key,[]),list) and all(isinstance(v,str) for v in facts.get(key,[])),key+'必须为字符串数组')
    task_id=raw.get('task_id')
    if task_id:
        task=store.c.execute('SELECT knowledge_id FROM lesson_tasks WHERE id=? LIMIT 1',(task_id,)).fetchone()
        require(task and (point is None or task['knowledge_id']==point['id']),'报告任务与知识点不一致','REPORT_CONFLICT')
        if point is None:point=one(store.c,'knowledge_points',task['knowledge_id'])
    knowledge_ids,point=report_points(store,raw,point)
    formal=raw.get('user_delivered',store.config('materials',store.c).get('report_policy',{}).get('user_delivered_is_authentic',False))
    require(type(formal) is bool,'user_delivered必须为布尔值')
    metadata={'intake_only':not formal,'knowledge_id':point['id'] if point else None,'knowledge_external_id':point['external_id'] if point else None,
              'knowledge_ids':knowledge_ids,'knowledge_external_ids':[one(store.c,'knowledge_points',x)['external_id'] for x in knowledge_ids],
              'source_ref':raw['source_ref'],'source_sha256':source_hash,'source_format':source_path.suffix or '.bin',
              'reported_date':day(raw['reported_date']).isoformat() if raw.get('reported_date') else None,'reported_task_id':task_id,
              'reported_summary':raw['reported_summary'],'reported_facts':facts,
              'evidence_status':'USER_CONFIRMED_LEARNING' if formal else 'NARRATIVE_ONLY_UNVERIFIED',
              'missing':[] if formal else ['COMPANION_JSON_MISSING','SESSION_UNBOUND','TASK_VERSION_UNCONFIRMED',
                         'VERBATIM_ATTEMPTS_MISSING','MATERIAL_VERSION_UNCONFIRMED'],
              'note':'用户交付的真实学习报告，正式保存教学事实；独立掌握另行测试，未知字段留空。' if formal else '叙述内容仅按回传报告保存；不构造原答、会话、时长、成绩或每日安排。'}
    previous=store.c.execute("SELECT * FROM artifacts WHERE kind='reports' AND json_extract(data,'$.source_sha256')=? AND json_extract(data,'$.knowledge_id') IS ? AND status<>'SUPERSEDED'",
                             (source_hash,point['id'] if point else None)).fetchone()
    if previous:
        previous_data=json.loads(previous['data'])
        previous_ids=previous_data.get('knowledge_ids') or ([previous_data['knowledge_id']] if previous_data.get('knowledge_id') else [])
        keys=('knowledge_id','source_sha256','reported_date','reported_task_id','reported_summary','reported_facts')
        require(previous_ids==metadata['knowledge_ids'] and all(previous_data.get(key)==metadata.get(key) for key in keys),
                '相同原件的提取说明不一致，请追加核验更正','REPORT_CONFLICT')
        if formal and previous_data.get('intake_only'):
            return confirm_learning_report(store,{'id':previous['id'],'user_delivered':True,'authorization_ref':raw['source_ref']})
        return {'artifact_id':previous['id'],'status':previous['status'],'duplicate_report':True,'missing':metadata['missing']}
    status='ACCEPTED' if formal else 'PENDING'
    aid=store.artifact('reports',task_id or (point['id'] if point else 'user-delivered-unassigned'),source,suffix=source_path.suffix or '.bin',data=metadata,status=status)
    store.event('report',aid,'received',{'status':status,'knowledge_id':point['id'] if point else None,
                'knowledge_ids':knowledge_ids,'source_sha256':source_hash})
    result=record_user_learning(store,aid,metadata) if formal else {}
    return {'artifact_id':aid,'status':status,'evidence_status':metadata['evidence_status'],'missing':metadata['missing'],**result}


def record_user_learning(store,artifact_id,metadata):
    """Record an actual reported learning batch with date-only or unknown timing."""
    from .learning import lesson_create,set_knowledge
    if not metadata.get('knowledge_id'):return {'association_status':'UNASSIGNED','note':'正式原报告已保存，知识点关联待提取，不猜测学习内容。'}
    point=one(store.c,'knowledge_points',metadata['knowledge_id'])
    task_id='reported-'+artifact_id
    lesson_create(store,{'id':task_id,'knowledge_id':point['id'],'goal':metadata['reported_summary'],
        'strategy':'按用户回传归档实际学习；内容与学习量由用户决定，不安排每日任务。',
        'acceptance':json.loads(point['data'])['criteria'],'next_task':'用户自行决定；独立掌握另行测试。',
        'arrangement_status':'RECORDED_FROM_REPORT','source_report_id':artifact_id,
        'reported_task_id':metadata.get('reported_task_id'),'material_usage_status':'NOT_SPECIFIED_IN_REPORT'})
    sid=uid();rid=uid();date=metadata.get('reported_date')
    precision='DATE_ONLY' if date else 'UNKNOWN'
    facts=metadata['reported_facts']
    checkpoint=metadata['reported_summary']
    data={'source_report_id':artifact_id,'record_kind':'USER_REPORTED_LEARNING','actual_date':date,
          'time_precision':precision,'actual_started_at':None,'actual_ended_at':None,
          'minutes':None,'time_basis':None,'covered':facts.get('covered',[]),'checkpoint':checkpoint,
          'reported_summary':metadata['reported_summary'],'reported_facts':facts,
          'unfinished_training':facts.get('unverified',[]),'mastery_status':'NOT_INDEPENDENTLY_TESTED',
          'next_task':'由用户自行决定学习内容与数量。'}
    store.c.execute('INSERT INTO learning_sessions VALUES(?,?,?,?,?,?)',(sid,task_id,1,'COMPLETED',date or '',1))
    store.c.execute('INSERT INTO session_records VALUES(?,?,?,?,?,?)',(rid,sid,date or '',None,checkpoint,canonical(data)))
    store.event('session',sid,'completed',{'record_id':rid,'source_report_id':artifact_id,
                'actual_date':date,'time_precision':precision,'recorded_from_user_report':True})
    store.c.execute("INSERT INTO meta VALUES('active_task',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(task_id,))
    store.event('selection',task_id,'report_archived',{'task_id':task_id,'version':1})
    # A real learning date is enough to anchor the first spaced review. Without a
    # date we stay unscheduled rather than inventing a zero-hour timestamp.
    ids=metadata.get('knowledge_ids') or [point['id']]
    covered=[];reviews=[]
    for identifier in ids:
        item=one(store.c,'knowledge_points',identifier)
        before=item['status']
        if before in ('UNASSESSED','UNLEARNED','LEARNING'):
            # A delivered report closes this teaching round. It is still not mastery:
            # the point waits for an independent assessment.
            set_knowledge(store,identifier,'ASSESSMENT_PENDING',artifact_id)
            after='ASSESSMENT_PENDING'
        else:
            after=before
        if identifier!=point['id']:
            store.event('knowledge',identifier,'covered_by_report',{'artifact_id':artifact_id,
                        'primary_knowledge_id':point['id'],'before':before,'after':after})
        item_review=ensure_first_review(store,identifier,date)
        if item_review:reviews.append(item_review)
        covered.append({'id':identifier,'external_id':item['external_id'],'name':item['name'],
                        'before':before,'after':after})
    review=next((item for item in reviews if item['knowledge_id']==point['id']),None)
    updated={**metadata,'knowledge_ids':ids,'covered_knowledge':covered,'session_id':sid,'session_record_id':rid,
             'archival_task_id':task_id,'archival_task_version':1,
             'time_precision':precision,'actual_date':date,'mastery_status':'NOT_INDEPENDENTLY_TESTED'}
    store.c.execute('UPDATE artifacts SET data=? WHERE id=?',(canonical(updated),artifact_id))
    return {'session_id':sid,'session_record_id':rid,'task_id':task_id,'time_precision':precision,
            'knowledge_ids':ids,'covered':covered,'review':review,'reviews':reviews}


def confirm_learning_report(store,raw):
    """Append a formal record for a previously pending user-delivered report."""
    from pathlib import Path
    original=one(store.c,'artifacts',raw.get('id'));metadata=json.loads(original['data'])
    require(original['kind']=='reports' and 'intake_only' in metadata
            and (metadata['intake_only'] or not metadata.get('knowledge_id')),'只能追加确认已接收的报告或补充未关联报告')
    require(raw.get('user_delivered') is True,'需要用户交付依据')
    text(raw.get('authorization_ref'),'用户交付及学习事实确认依据')
    existing=store.c.execute("SELECT * FROM artifacts WHERE kind='reports' AND json_extract(data,'$.confirmed_from_artifact_id')=?",(original['id'],)).fetchone()
    if existing:return {'artifact_id':existing['id'],'status':existing['status'],'duplicate_report':True}
    source=(store.root/original['path']).read_bytes()
    require(digest(source)==original['sha256'],'原报告哈希不符','ARTIFACT_HASH')
    if raw.get('knowledge_id'):
        point=one(store.c,'knowledge_points',raw['knowledge_id'])
        require(not metadata.get('knowledge_id') or metadata['knowledge_id']==point['id'],'不能更改已确认的知识点','REPORT_CONFLICT')
        metadata={**metadata,'knowledge_id':point['id'],'knowledge_external_id':point['external_id']}
    if raw.get('knowledge_ids') is not None:
        point=one(store.c,'knowledge_points',metadata['knowledge_id']) if metadata.get('knowledge_id') else None
        ids,point=report_points(store,raw,point)
        metadata={**metadata,'knowledge_id':point['id'],'knowledge_external_id':point['external_id'],'knowledge_ids':ids}
    metadata={**metadata,'intake_only':False,'evidence_status':'USER_CONFIRMED_LEARNING','missing':[],
              'source_format':metadata.get('source_format',Path(original['path']).suffix),
              'confirmed_from_artifact_id':original['id'],'authorization_ref':raw['authorization_ref'],
              'note':'用户交付的正式真实学习报告；缺少配套JSON不影响学习事实归档，独立掌握另行测试。'}
    aid=store.artifact('reports',original['entity_id'],source,suffix=Path(original['path']).suffix,
                      data=metadata,version=original['version']+1,status='ACCEPTED')
    store.event('report',aid,'learning_confirmed',{'original_artifact_id':original['id'],'authorization_ref':raw['authorization_ref']})
    result=record_user_learning(store,aid,metadata)
    store.c.execute("UPDATE artifacts SET status='SUPERSEDED' WHERE id=?",(original['id'],))
    store.event('report',original['id'],'superseded_by_confirmed_learning',{'replacement':aid})
    return {'artifact_id':aid,'status':'ACCEPTED',**result}


def field_value(value):
    return value if isinstance(value,str) else canonical(value)


def fields(data):
    out={'report_id':data.get('report_id'),'version':data.get('version'),'task_id':data.get('task_id'),
         'session_id':data.get('session_id'),'covered':data.get('covered',[]),'checkpoint':data.get('checkpoint'),
         'unfinished_training':data.get('unfinished_training',[]),'minutes':data.get('minutes')}
    for a in data.get('attempts',[]):
        for key in ('question','answer','process','assistance','form','dimensions','answer_seen'):
            out[a['id']+'.'+key]=a.get(key)
        for key in ('result','basis','reference','error_reason','error_type'):
            out[a['id']+'.evaluation.'+key]=a.get('evaluation',{}).get(key)
    return out


def markdown_template(data):
    lines=['# 学习成果报告','以下为实际教学成果，未完成和未知项如实注明。']
    for key,value in fields(data).items():
        lines+=['## '+key,'<!-- studyos-field:'+key+' -->',field_value(value),'<!-- /studyos-field -->']
    lines+=['## 教学分析与后续建议','在此补充完整讲解、纠正过程、强化训练和未解决问题。']
    return '\n\n'.join(lines)+'\n'


def validate_pair(markdown,data):
    differences=[]
    from .validation import report_shape
    report_shape(data)
    require(isinstance(data,dict),'报告JSON必须为对象')
    for key in ('report_id','task_id','session_id','checkpoint'): text(data.get(key),key)
    require(type(data.get('version')) is int and data['version']>=1,'报告版本无效')
    require(isinstance(data.get('attempts'),list),'attempts 必须为数组')
    ids=[a.get('id') for a in data['attempts']]
    require(all(isinstance(x,str) and re.fullmatch(r'[A-Za-z0-9_-]+',x) for x in ids) and len(set(ids))==len(ids),'题目作答ID必须唯一且格式有效')
    for key,value in fields(data).items():
        pattern=r'<!-- studyos-field:'+re.escape(key)+r' -->\s*\n(.*?)\n\s*<!-- /studyos-field -->'
        matches=re.findall(pattern,markdown,re.S)
        if len(matches)!=1 or matches[0].strip()!=field_value(value).strip(): differences.append(key)
    return differences


def import_report(store,raw):
    md_path=raw.get('markdown_path');json_path=raw.get('json_path')
    from pathlib import Path
    markdown=Path(md_path).read_text(encoding='utf-8-sig');data=load(json_path)
    differences=validate_pair(markdown,data)
    require(raw.get('markdown_sha256')==digest(Path(md_path).read_bytes()) and raw.get('json_sha256')==digest(Path(json_path).read_bytes()),'输入报告在导入期间变化','REPORT_CHANGED')
    task=store.c.execute('SELECT * FROM lesson_tasks WHERE id=? ORDER BY version DESC LIMIT 1',(data['task_id'],)).fetchone()
    require(task,'报告任务不存在','NOT_FOUND')
    if data.get('knowledge_external_id') is not None:
        point=one(store.c,'knowledge_points',task['knowledge_id'])
        if data['knowledge_external_id']!=point['external_id']:differences.append('knowledge_external_id')
    session=one(store.c,'learning_sessions',data['session_id'])
    require(session['task_id']==task['id'],'报告会话归属错误')
    if data.get('task_version')!=task['version'] or session['task_version']!=task['version']: differences.append('task_version')
    semantic=raw.get('semantic_review_ref')
    if not semantic: differences.append('semantic_review_required')
    existing=store.c.execute("SELECT * FROM artifacts WHERE kind='reports' AND entity_id=?",(task['id'],)).fetchall()
    for row in existing:
        metadata=json.loads(row['data'])
        if metadata.get('report_id')==data['report_id'] and row['version']==data['version']:
            require(row['sha256']==raw['markdown_sha256'] and metadata.get('json_sha256')==raw['json_sha256'],'同报告版本内容冲突','REPORT_CONFLICT')
            return {'artifact_id':row['id'],'status':row['status'],'differences':metadata.get('differences',[]),'duplicate_report':True}
    prior=[r for r in existing if json.loads(r['data']).get('report_id')==data['report_id']]
    if prior: require(data['version']>max(r['version'] for r in prior),'报告版本必须递增','STALE_REVISION')
    prior_accepted=[r for r in prior if r['status']=='ACCEPTED']
    previous_report=max(prior_accepted,key=lambda r:r['version']) if prior_accepted else None
    if previous_report: text(raw.get('reason'),'已接受报告修订理由')
    if session['status']=='COMPLETED' and not previous_report and not raw.get('supersedes_record_id'):
        differences.append('completed_session_requires_batch_reference')
    for attempt in data['attempts']:
        saved=store.c.execute('SELECT * FROM attempts WHERE id=?',(attempt['id'],)).fetchone()
        if not saved: continue
        previous=json.loads(saved['data'])
        keys=('question','answer','process','form','dimensions','assistance','answer_seen')
        if not previous_report or saved['session_id']!=data['session_id'] or any(previous.get(key)!=attempt.get(key) for key in keys):
            differences.append('immutable_original:'+attempt['id'])
        last=store.c.execute('SELECT * FROM attempt_evaluations WHERE attempt_id=? ORDER BY revision DESC LIMIT 1',(attempt['id'],)).fetchone()
        if last and canonical(json.loads(last['data']))!=canonical(attempt.get('evaluation',{})):
            if attempt.get('expected_evaluation_revision')!=last['revision']: differences.append('evaluation_revision:'+attempt['id'])
    status='PENDING' if differences else 'ACCEPTED'
    metadata={'report_id':data['report_id'],'differences':differences,'json_sha256':raw['json_sha256'],'semantic_review_ref':semantic}
    jid=store.artifact('report_data',task['id'],Path(json_path).read_bytes(),data={'report_id':data['report_id']},version=data['version'],status=status)
    aid=store.artifact('reports',task['id'],Path(md_path).read_bytes(),suffix='.md',data={**metadata,'json_artifact_id':jid},version=data['version'],status=status)
    store.event('report',aid,'imported',{'status':status,'task_id':task['id'],'report_id':data['report_id'],'version':data['version'],'differences':differences})
    if not differences:
        from .learning import add_attempt,settle,set_knowledge
        for attempt in data['attempts']:
            saved=store.c.execute('SELECT * FROM attempts WHERE id=?',(attempt['id'],)).fetchone()
            if not saved:
                add_attempt(store,{**attempt,'knowledge_id':task['knowledge_id'],'session_id':session['id'],
                                   'source':'gpt_teaching','confirmed':True,'request_id':raw['request_id']})
            else:
                last=store.c.execute('SELECT * FROM attempt_evaluations WHERE attempt_id=? ORDER BY revision DESC LIMIT 1',(attempt['id'],)).fetchone()
                if canonical(json.loads(last['data']))!=canonical(attempt['evaluation']):
                    from .app import evaluate_attempt
                    evaluate_attempt(store,{'id':attempt['id'],'evaluation':attempt['evaluation'],'expected_revision':last['revision'],'reason':raw['reason']})
        prior_record=json.loads(previous_report['data']).get('session_record_id') if previous_report else raw.get('supersedes_record_id')
        batch=settle(store,{'session_id':session['id'],'expected_revision':session['revision'],'minutes':data.get('minutes'),
                      'time_basis':data.get('time_basis'),'covered':data.get('covered',[]),'checkpoint':data['checkpoint'],
                      'unfinished_training':data.get('unfinished_training',[]),'next_task':data.get('next_task','安排Codex独立考核'),
                      'supersedes_record_id':prior_record,'reason':raw.get('reason')},report_append=True)
        store.c.execute('UPDATE artifacts SET data=? WHERE id=?',(canonical({**metadata,'json_artifact_id':jid,'session_record_id':batch['record_id']}),aid))
        current=one(store.c,'knowledge_points',task['knowledge_id'])
        if current['status'] not in ('MASTERED','CONSOLIDATE'): set_knowledge(store,task['knowledge_id'],'ASSESSMENT_PENDING',aid)
        if prior:
            for older in prior:
                if older['status']=='PENDING':
                    store.c.execute("UPDATE artifacts SET status='SUPERSEDED' WHERE id=?",(older['id'],))
                    store.event('report',older['id'],'resolved_by_new_version',{'replacement':aid})
            store.event('report',aid,'supersedes',{'prior_artifact_ids':[r['id'] for r in prior],'reason':raw.get('reason','新版核验报告')})
    return {'artifact_id':aid,'status':status,'differences':differences,'json_artifact_id':jid}
