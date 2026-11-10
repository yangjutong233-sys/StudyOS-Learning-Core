import copy
import json
from pathlib import Path
from .common import Error, SUBJECTS, canonical, digest, load, number, require, sha, text, uid
from .storage import Store, one, rows
from . import learning, materials, reports, reviews, schedule


def update_materials(store,raw):
    config=raw.get('config');require(isinstance(config,dict),'config 必须为材料配置')
    require(isinstance(config.get('subjects'),dict) and set(config['subjects'])==set(SUBJECTS),'需要四科材料配置')
    require(config.get('root') is None or isinstance(config['root'],str),'材料根目录无效')
    if 'learning_control' in config:
        control=config['learning_control']
        require(isinstance(control,dict) and control.get('daily_planning')=='USER_ONLY'
                and control.get('gpt_handoff')=='PROGRESS_ONLY','learning_control必须明确USER_ONLY及PROGRESS_ONLY')
    if 'teaching_preferences' in config:
        preferences=config['teaching_preferences']
        require(isinstance(preferences,dict) and all(isinstance(k,str) and isinstance(v,list)
                and all(isinstance(item,str) for item in v) for k,v in preferences.items()),'teaching_preferences必须按科目范围保存字符串数组')
    if 'report_policy' in config:
        policy=config['report_policy']
        require(isinstance(policy,dict) and policy.get('user_delivered_is_authentic') is True
                and policy.get('formats')=='ANY' and policy.get('independent_mastery_requires_test') is True,
                'report_policy须明确用户报告正式归档、格式不限、独立掌握需测试')
    if 'web_progress_summaries' in config:
        summaries=config['web_progress_summaries']
        require(isinstance(summaries,dict),'web_progress_summaries必须为对象')
        for summary in summaries.values():
            require(isinstance(summary,dict) and isinstance(summary.get('sources'),dict)
                    and isinstance(summary.get('sections'),dict),'网页版摘要须包含来源和章节对象')
            require(all(isinstance(v,list) and all(isinstance(item,str) for item in v) for v in summary['sources'].values()),'摘要来源须为记录ID数组')
            for key in ('preference_source','teaching_preferences'):
                require(isinstance(summary.get(key,[]),list) and all(isinstance(item,str) for item in summary.get(key,[])),'摘要教学偏好须为文本数组')
            require(set(summary['sections'])<=set(('已学内容','理解情况','尚未确认','已纠正的误区','学习断点')),'网页版摘要只保存教学进度')
            for value in summary['sections'].values():
                require(value is None or isinstance(value,str) or isinstance(value,list)
                        and all(isinstance(item,str) for item in value),'网页版摘要内容须为文本或文本数组')
    for spec in config['subjects'].values():
        require(isinstance(spec,dict) and (spec.get('path') is None or isinstance(spec.get('path'),str)) and isinstance(spec.get('files'),dict),'科目目录配置无效')
        for key,item in spec['files'].items():
            text(key,'材料键');require(isinstance(item,dict),'材料项必须为对象');text(item.get('path'),'材料路径')
    from .subject_handoffs import validate_scopes
    validate_scopes(store,config)
    text(raw.get('reason'),'材料路径修改理由')
    return store.configure(raw,'materials',config)


def update_policy(store,raw):
    name=raw.get('name');require(name in ('review_policy','mastery_policy'),'只支持复习和掌握规则')
    cfg=raw.get('config');require(isinstance(cfg,dict),'config 必须为对象');text(raw.get('reason'),'规则变更理由')
    if name=='review_policy':
        gaps=cfg.get('intervals')
        require(isinstance(gaps,list) and gaps and all(type(x) is int and 1<=x<=3650 for x in gaps) and gaps==sorted(gaps),'间隔必须为递增正整数数组')
        require(type(cfg.get('failure_days')) is int and cfg['failure_days']>=1,'failure_days无效')
        require(type(cfg.get('mistake_delay_default')) is int and cfg['mistake_delay_default']>=0,'mistake_delay_default无效')
        overrides=cfg.get('subject_overrides',{})
        require(isinstance(overrides,dict) and set(overrides)<=set(SUBJECTS),'科目复习规则无效')
        for spec in overrides.values():
            require(isinstance(spec,dict) and spec.get('basis')=='first_learning_date','科目规则必须明确首次学习日基准')
            offsets=spec.get('intervals')
            require(isinstance(offsets,list) and offsets and all(type(x) is int and 1<=x<=3650 for x in offsets)
                    and offsets==sorted(set(offsets)),'首次学习日偏移须为严格递增正整数')
            for key in ('failure_days','partial_days','recovery_passes'):
                require(type(spec.get(key)) is int and 1<=spec[key]<=3650,key+'无效')
    else:
        require(set(cfg.get('dimensions',{}))==set(SUBJECTS),'必须定义四科维度')
        for values in cfg['dimensions'].values():
            require(isinstance(values,list) and values and all(isinstance(x,str) and x for x in values),'维度无效')
    return store.configure(raw,name,cfg)


def evaluate_attempt(store,raw):
    a=one(store.c,'attempts',raw.get('id'));e=raw.get('evaluation',{})
    require(e.get('result') in ('PASS','PARTIAL','FAIL','UNDETERMINED'),'判定无效')
    text(e.get('basis'),'basis');text(e.get('reference'),'reference');text(raw.get('reason'),'reason')
    latest=store.c.execute('SELECT MAX(revision) FROM attempt_evaluations WHERE attempt_id=?',(a['id'],)).fetchone()[0]
    require(raw.get('expected_revision')==latest,'判定版本冲突','STALE_REVISION')
    aid=a['assessment_id']
    # Correcting settled evidence cannot silently leave an obsolete mastery projection.
    if aid:
        test=one(store.c,'assessments',aid)
        require(test['status']=='OPEN','已结算考核需建立新复核测试，不能静默更改结论','ASSESSMENT_CLOSED')
    identifier=uid()
    store.c.execute('INSERT INTO attempt_evaluations VALUES(?,?,?,?,?,?)',(identifier,a['id'],store.at,latest+1,e['result'],canonical(e)))
    store.event('evaluation',identifier,'correction',{'attempt_id':a['id'],'revision':latest+1,'reason':raw['reason'],'evaluation':e})
    store.event('attempt',a['id'],'reevaluated',{'evaluation_id':identifier,'revision':latest+1})
    return {'id':identifier,'attempt_id':a['id'],'revision':latest+1}


def progress(store,raw):
    p=store.c.execute('SELECT * FROM daily_plans WHERE id=? ORDER BY version DESC LIMIT 1',(raw.get('plan_id'),)).fetchone()
    require(p,'计划不存在','NOT_FOUND');require(raw.get('expected_revision')==p['version'],'计划版本已变化','STALE_REVISION')
    spec,h=schedule.capacity(store,store.c,p['day'])
    require(h==p['schedule_hash'],'时间配置已变化，先重排计划','STALE_REVISION')
    item=store.c.execute('SELECT * FROM plan_items WHERE id=? AND plan_id=? AND plan_version=?',(raw.get('id'),p['id'],p['version'])).fetchone()
    require(item,'任务不存在','NOT_FOUND')
    status=raw.get('status');require(status in ('ACTIVE','COMPLETED','DEFERRED'),'任务状态无效')
    require(item['status']!='COMPLETED','已完成任务不可覆盖')
    last=store.c.execute("SELECT new_revision FROM audit_events WHERE entity_type='plan_item' AND entity_id=? ORDER BY event_seq DESC LIMIT 1",(item['id'],)).fetchone()
    item_revision=last[0] if last else 0
    require(raw.get('expected_item_revision',0)==item_revision,'计划任务状态已变化','STALE_REVISION')
    text(raw.get('reason'),'reason')
    if status=='ACTIVE':
        proposed=json.loads(p['data'])
        # Evaluate the whole latest plan with its prospective state; never trust a
        # caller-supplied status in plan JSON or reuse DEFERRED's capacity exemption.
        schedule.validate_plan(store,store.c,proposed,status_overrides={item['id']:status})
    else:
        schedule.validate_review_binding(store.c,p['id'],json.loads(item['data']),status,item)
    store.c.execute('UPDATE plan_items SET status=? WHERE id=? AND plan_id=? AND plan_version=?',(status,item['id'],p['id'],p['version']))
    store.event('plan_item',item['id'],'progress',{'before':item['status'],'after':status,'reason':raw['reason'],'plan_version':p['version']})
    return {'id':item['id'],'status':status,'item_revision':item_revision+1}


WRITE = {
 'policy.update':update_policy,
 'knowledge.import':materials.knowledge,'materials.update':update_materials,'materials.register':materials.register,
 'lesson.create':learning.lesson_create,'lesson.prepare':learning.prepare,
 'knowledge.pending':learning.mark_pending_from_report,
 'session.duration':learning.record_duration,
 'lesson.start':lambda s,r:learning.prepare(s,r,True),
 'lesson.pause':learning.settle,'lesson.finish':lambda s,r:learning.settle(s,r,True),
 'attempt.add':learning.add_attempt,'attempt.evaluate':evaluate_attempt,
 'assessment.create':learning.create_assessment,'assessment.submit':learning.submit_assessment,
 'report.import':reports.import_report,'report.resolve':reports.import_report,'report.receive':reports.receive_report,
 'report.confirm-learning':reports.confirm_learning_report,
 'mistake.record-event':learning.mistake_event,
 'review.create':reviews.create_series,'review.complete':reviews.complete,
 'review.reschedule':reviews.adjust,'review.start':lambda s,r:reviews.adjust(s,r,start=True),
 'review.cancel':lambda s,r:reviews.adjust(s,r,cancel=True),'review.reopen':reviews.reopen,
 'schedule.confirm':lambda s,r:schedule.change(s,r,True),'schedule.update':schedule.change,
 'schedule.exception.add':schedule.exception,
 'plan.apply':schedule.apply_plan,'plan.revise':schedule.apply_plan,'plan.record-progress':progress,
}


def execute(store,operation,raw=None):
    from .validation import validate_request
    validate_request(operation,{} if raw is None else raw)
    raw=copy.deepcopy({} if raw is None else raw)
    if operation in WRITE:
        if operation=='report.receive':
            if raw.get('source_path'):raw['source_sha256']=sha(raw['source_path'])
            else:raw['markdown_sha256']=sha(raw['markdown_path'])
        elif operation in ('report.import','report.resolve'):
            raw['markdown_sha256']=sha(raw['markdown_path']);raw['json_sha256']=sha(raw['json_path'])
        if raw.get('dry_run'):
            require(operation in ('schedule.update','schedule.confirm','materials.update'),'该操作不支持dry-run')
            current=store.config('materials' if operation=='materials.update' else 'schedule')
            if operation.startswith('schedule.'): schedule.validate(raw.get('config',current))
            return {'dry_run':True,'before':current,'after':raw.get('config',current)}
        return store.run(operation,raw,lambda r:WRITE[operation](store,r))
    if operation=='init': return store.initialize()
    if operation=='report.validate':
        markdown=Path(raw['markdown_path']).read_text(encoding='utf-8-sig');data=load(raw['json_path'])
        differences=reports.validate_pair(markdown,data)
        return {'valid':not differences,'differences':differences,'semantic_review_required':True}
    if operation=='recover':
        from .storage import recover
        return recover(store)
    if operation in ('handoff.ensure','handoff.export','teaching.export'):
        from .handoff import handoff
        return handoff(store,raw.get('target','gpt' if operation=='teaching.export' else 'both'),operation=='handoff.ensure')
    if operation=='backup.create':
        from .maintenance import create_backup
        return create_backup(store,raw.get('mode','personal'))
    if operation in ('backup.verify','restore.verify'):
        from .maintenance import verify_backup,restore_backup
        if operation=='backup.verify': return {'verified':True,'manifest':verify_backup(raw['path'])}
        import tempfile
        with tempfile.TemporaryDirectory(prefix='studyos-verify-') as tmp:
            return restore_backup(raw['path'],Path(tmp)/'restored',store.root)
    if operation=='restore.restore':
        from .maintenance import restore_backup
        return restore_backup(raw['path'],raw['destination'],store.root)
    if operation.startswith('migrate.'):
        from .migration import dispatch
        return dispatch(store,operation,raw)
    with store.connect(True) as c:
        return query(store,c,operation,raw)


def query(store,c,op,r):
    limit=r.get('limit',20);cursor=r.get('cursor',0)
    require(type(limit) is int and 1<=limit<=100 and type(cursor) is int and cursor>=0,'分页参数无效')
    if op in ('summary','status.today','planning-context'):
        from .handoff import context
        meta,task,k,config=context(store,c)
        result={'metadata':meta,'at':store.at,'selected_task':task,'knowledge':k,
                'knowledge_counts':rows(c,'SELECT c.subject,k.status,COUNT(*) AS count FROM knowledge_points k JOIN catalogs c ON c.id=k.catalog_id GROUP BY c.subject,k.status'),
                'due_reviews':rows(c,"SELECT id,series_id,round,due_date,result FROM review_tasks WHERE status IN ('PENDING','ACTIVE') AND due_date<=? ORDER BY due_date LIMIT ?",(store.at[:10],limit)),
                'pending_reports':rows(c,"SELECT id,entity_id,path FROM artifacts WHERE kind='reports' AND status='PENDING' LIMIT ?",(limit,)),
                'schedule_status':config['schedule']['status']}
        time_summary=c.execute("""SELECT COALESCE(SUM(minutes),0),COALESCE(SUM(CASE WHEN minutes IS NULL THEN 1 ELSE 0 END),0)
          FROM session_records WHERE id NOT IN
          (SELECT json_extract(data,'$.supersedes_record_id') FROM session_records
           WHERE json_extract(data,'$.supersedes_record_id') IS NOT NULL)""").fetchone()
        result['learning_time']={'confirmed_minutes':time_summary[0],'unknown_batches':time_summary[1]}
        result['learning_reports']={'accepted_user_reports':c.execute("SELECT COUNT(*) FROM artifacts WHERE kind='reports' AND status='ACCEPTED' AND json_extract(data,'$.evidence_status')='USER_CONFIRMED_LEARNING'").fetchone()[0],
            'unassigned_user_reports':c.execute("SELECT COUNT(*) FROM artifacts WHERE kind='reports' AND status='ACCEPTED' AND json_extract(data,'$.evidence_status')='USER_CONFIRMED_LEARNING' AND json_extract(data,'$.knowledge_id') IS NULL").fetchone()[0]}
        if op=='planning-context':
            spec,h=schedule.capacity(store,c,r.get('day',store.at[:10]),False)
            result['capacity']={'day':r.get('day',store.at[:10]),'spec':spec,'schedule_hash':h,'confirmed':config['schedule']['status']=='confirmed'}
        return result
    if op=='knowledge.list':
        return rows(c,"SELECT k.id,k.external_id,k.name,k.status,c.subject FROM knowledge_points k JOIN catalogs c ON c.id=k.catalog_id WHERE (? IS NULL OR c.subject=?) ORDER BY c.id,COALESCE(json_extract(k.data,'$.source_order'),-1),k.external_id LIMIT ? OFFSET ?",(r.get('subject'),r.get('subject'),limit,cursor))
    if op=='knowledge.show':
        k=one(c,'knowledge_points',r.get('id'));k['data']=json.loads(k['data']);k['dependencies']=rows(c,'SELECT * FROM knowledge_dependencies WHERE knowledge_id=?',(k['id'],));return k
    if op=='lesson.show':
        t=learning.latest_task(c,r.get('id'))
        return {'task':t,'sessions':rows(c,'SELECT * FROM learning_sessions WHERE task_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(t['id'],limit,cursor)),
                'records':rows(c,'SELECT r.* FROM session_records r JOIN learning_sessions s ON s.id=r.session_id WHERE s.task_id=? ORDER BY r.rowid DESC LIMIT ? OFFSET ?',(t['id'],limit,cursor))}
    if op=='weaknesses':
        return rows(c,"SELECT id,knowledge_id,status,cause,data FROM mistakes WHERE status<>'RESOLVED' ORDER BY id LIMIT ? OFFSET ?",(limit,cursor))
    if op=='materials.show': return store.config('materials',c)
    if op=='policy.show':
        require(r.get('name') in ('review_policy','mastery_policy'),'name必须为review_policy或mastery_policy')
        return store.config(r['name'],c)
    if op=='materials.check': return materials.check(store,c,r.get('subject'))
    if op in ('materials.locate','materials.read'):
        return materials.locate(store,c,r.get('knowledge_id') or r.get('id'),r.get('level',1),r.get('reason'),op=='materials.read',
                                r.get('material_id'),r.get('material_version'),r.get('task_id'),r.get('task_version'))
    if op in ('schedule.show','schedule.validate'):
        config=store.config('schedule',c);schedule.validate(config);return config
    if op=='schedule.exception.list': return rows(c,'SELECT * FROM schedule_exceptions ORDER BY day LIMIT ? OFFSET ?',(limit,cursor))
    if op=='plan.validate': return {'valid':True,'schedule_hash':schedule.validate_plan(store,c,r)}
    if op=='plan.show':
        p=c.execute('SELECT * FROM daily_plans WHERE id=? ORDER BY version DESC LIMIT 1',(r.get('id'),)).fetchone()
        require(p,'计划不存在','NOT_FOUND');p=dict(p)
        spec,h=schedule.capacity(store,c,p['day'],False);p['stale']=h!=p['schedule_hash'];p['items']=rows(c,'SELECT * FROM plan_items WHERE plan_id=? AND plan_version=?',(p['id'],p['version']));return p
    if op in ('review.due','review.history'):
        if op=='review.due': return rows(c,"SELECT * FROM review_tasks WHERE status IN ('PENDING','ACTIVE') AND due_date<=? ORDER BY due_date,id LIMIT ? OFFSET ?",(r.get('day',store.at[:10]),limit,cursor))
        return rows(c,'SELECT * FROM review_tasks WHERE series_id=? ORDER BY round LIMIT ? OFFSET ?',(r.get('id'),limit,cursor))
    if op=='attempt.list': return rows(c,'SELECT a.* FROM attempts a JOIN questions q ON q.id=a.question_id AND q.version=a.question_version WHERE (? IS NULL OR q.knowledge_id=?) ORDER BY a.rowid LIMIT ? OFFSET ?',(r.get('knowledge_id'),r.get('knowledge_id'),limit,cursor))
    if op in ('assessment.show','mistake.show'): return one(c,'assessments' if op=='assessment.show' else 'mistakes',r.get('id'))
    if op=='mistake.list': return rows(c,'SELECT * FROM mistakes ORDER BY id LIMIT ? OFFSET ?',(limit,cursor))
    if op in ('mastery.show','mastery.history','mastery.evaluate'):
        values=rows(c,'SELECT * FROM mastery_assessments WHERE knowledge_id=? ORDER BY rowid DESC LIMIT ? OFFSET ?',(r.get('knowledge_id') or r.get('id'),1 if op=='mastery.show' else limit,cursor))
        return {'history':values,'note':'查询证据；新考核通过assessment提交，禁止无证据批量改状态'}
    if op=='report.show':
        a=one(c,'artifacts',r.get('id'));p=store.root/a['path'];require(sha(p)==a['sha256'],'报告损坏','ARTIFACT_HASH')
        try:content=p.read_text(encoding='utf-8-sig') if p.suffix.lower() in ('.md','.txt','.json','.csv','.log','.yaml','.yml') else None
        except UnicodeDecodeError:content=None
        return {'artifact':a,'path':str(p),'text':content}
    if op=='handoff.show':
        from .handoff import show
        return show(store,c,r)
    if op in ('audit.list','audit.show'):
        return rows(c,'SELECT * FROM audit_events WHERE (? IS NULL OR entity_id=?) AND (? IS NULL OR entity_type=?) AND (? IS NULL OR request_id=?) ORDER BY event_seq LIMIT ? OFFSET ?',
                    (r.get('id'),r.get('id'),r.get('entity_type'),r.get('entity_type'),r.get('request_id'),r.get('request_id'),limit,cursor))
    if op=='doctor':
        from .maintenance import doctor
        return doctor(store,c)
    raise Error('UNKNOWN_COMMAND','不支持的命令：'+op)
