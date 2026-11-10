from datetime import timedelta
import copy
import json
from .common import Error, canonical, day, digest, number, require, text, uid
from .storage import rows


def minute(value):
    require(isinstance(value,str),'时间必须为 HH:MM')
    try:
        h,m=map(int,value.split(':'))
        require(0<=h<=24 and 0<=m<60 and (h<24 or m==0),'时间超界')
        return 60*h+m
    except (ValueError,TypeError):
        raise Error('VALIDATION_ERROR','时间必须为 HH:MM')


def spans(values):
    require(isinstance(values,list),'窗口必须为数组')
    out=[]
    for pair in values:
        require(isinstance(pair,list) and len(pair)==2,'窗口必须为起止二元数组')
        a,b=map(minute,pair);require(a<b,'不支持跨日窗口，需按日拆分');out.append((a,b))
    out.sort()
    require(all(out[i][1]<=out[i+1][0] for i in range(len(out)-1)),'窗口重叠')
    return out


def validate(config):
    require(config.get('timezone')=='Asia/Shanghai','时区必须为 Asia/Shanghai')
    require(config.get('status') in ('draft','confirmed'),'时间状态无效')
    require(set(config.get('week',{}))==set(map(str,range(7))),'必须配置周一到周日(0..6)')
    for spec in config['week'].values():
        require(type(spec.get('rest')) is bool,'rest 必须为布尔值')
        windows=spans(spec.get('windows'));blocked=spans(spec.get('blocked',[]))
        require(type(spec.get('max_minutes')) is int,'max_minutes 必须为整数')
        number(spec['max_minutes'],'max_minutes',0,1440)
        require(not any(a<y and b>x for a,b in windows for x,y in blocked),'学习窗口侵占工作或休息时间','SCHEDULE_CONFLICT')
        require(not spec['rest'] or not windows,'休息日不能有学习窗口')
    return config


def change(store,raw,confirm=False):
    text(raw.get('authorization_ref'),'明确时间授权 authorization_ref')
    old=store.config('schedule',store.c)
    new=copy.deepcopy(old if confirm else raw.get('config'))
    require(isinstance(new,dict),'config 必须为完整时间配置')
    new['status']='confirmed';new['authorization_ref']=raw['authorization_ref']
    validate(new)
    if confirm: require(old['status']=='draft','正式配置请使用 update')
    else: require(old['status']=='confirmed','首次请 confirm 草案')
    return store.configure(raw,'schedule',new)


def exception(store,raw):
    d=day(raw.get('day')).isoformat()
    text(raw.get('authorization_ref'),'日期例外授权 authorization_ref')
    existing=store.c.execute('SELECT * FROM schedule_exceptions WHERE day=?',(d,)).fetchone()
    spec=raw.get('config');require(isinstance(spec,dict),'需要当天完整约束')
    base=store.config('schedule',store.c)
    validate({**base,'week':{str(i):spec for i in range(7)}})
    identifier=existing['id'] if existing else raw.get('id') or uid()
    if existing:
        require(raw.get('expected_revision')==existing['revision'],'日期例外版本冲突','STALE_REVISION')
        store.c.execute('UPDATE schedule_exceptions SET revision=revision+1,data=? WHERE id=?',(canonical(raw),identifier))
    else: store.c.execute('INSERT INTO schedule_exceptions VALUES(?,?,?,?)',(identifier,d,1,canonical(raw)))
    store.event('schedule_exception',identifier,'revised' if existing else 'created',raw,raw.get('expected_revision') if existing else None)
    return {'id':identifier,'day':d,'revision':existing['revision']+1 if existing else 1,'config':spec}


def capacity(store,c,date_value,confirmed=True):
    d=day(date_value);config=store.config('schedule',c);validate(config)
    if confirmed: require(config['status']=='confirmed','时间草案尚未确认','SCHEDULE_UNCONFIRMED')
    spec=config['week'][str(d.weekday())]
    ex=c.execute('SELECT data FROM schedule_exceptions WHERE day=?',(d.isoformat(),)).fetchone()
    if ex: spec=json.loads(ex[0])['config']
    return spec,digest(canonical({'config':config,'exception':json.loads(ex[0]) if ex else None}).encode())


def executable_date(store,c,suggested):
    d=day(suggested)
    if store.config('schedule',c)['status']!='confirmed': return suggested,True
    for _ in range(366):
        spec,_=capacity(store,c,d.isoformat())
        if not spec['rest'] and spec['windows'] and spec['max_minutes']>0: return d.isoformat(),False
        d+=timedelta(days=1)
    return suggested,True


def validate_review_binding(c,plan_id,item,status,existing=None):
    """Shared by plan creation/revision and every item-status write, in its transaction."""
    rid=item.get('review_id')
    if not rid:return
    review=c.execute('SELECT * FROM review_tasks WHERE id=?',(rid,)).fetchone()
    require(review,'复习不存在','INVALID_REVIEW')
    series=c.execute('SELECT * FROM review_series WHERE id=?',(review['series_id'],)).fetchone()
    members=json.loads(series['data']).get('members',[])
    require(series['knowledge_id']==item['knowledge_id'] or item['knowledge_id'] in members,
            '复习目标与任务不一致','INVALID_REVIEW')
    preserved=existing is not None and canonical(json.loads(existing['data']))==canonical(item)
    # Historical completed bindings and deferred placeholders do not reserve execution.
    if status=='DEFERRED' or (preserved and existing['status']=='COMPLETED' and status=='COMPLETED'):
        return
    require(not json.loads(review['data']).get('anchor_pending'),'复习首次学习日尚未确认，先补真实依据并重新安排','REVIEW_ANCHOR_REQUIRED')
    waiting=review['status']=='ACTIVE' and review['result']=='UNDETERMINED' and review['completed_at'] is None
    if status=='COMPLETED':
        require(preserved and review['status']!='CANCELLED','不能完成取消或无原绑定的复习项','INVALID_REVIEW')
    else:
        require(review['status']=='PENDING' or waiting or (preserved and review['status']=='ACTIVE'),
                '复习已结束或正在执行；仅待补证据轮次可新安排','INVALID_REVIEW')
    duplicate=c.execute('''SELECT i.plan_id,i.id FROM plan_items i
        WHERE i.review_id=? AND i.status IN ('PENDING','ACTIVE')
        AND i.plan_version=(SELECT MAX(p.version) FROM daily_plans p WHERE p.id=i.plan_id)
        AND NOT(i.plan_id=? AND i.id=?) LIMIT 1''',(rid,plan_id,item['id'])).fetchone()
    require(not duplicate,'复习已绑定其他待执行计划项，先延期原项再安排','DUPLICATE_REVIEW')


def validate_plan(store,c,raw,*,status_overrides=None):
    text(raw.get('id'),'id');day(raw.get('day'))
    spec,h=capacity(store,c,raw['day']);windows=spans(spec['windows']);blocked=spans(spec.get('blocked',[]))
    require(raw.get('schedule_hash')==h,'时间配置已变化，重新读取 planning-context','STALE_REVISION',{'current_hash':h})
    items=raw.get('items');require(isinstance(items,list),'items 必须为数组')
    previous=c.execute('SELECT * FROM daily_plans WHERE id=? ORDER BY version DESC LIMIT 1',(raw['id'],)).fetchone()
    saved={}
    if previous:
        require(previous['day']==raw['day'],'修订不能变更计划所属日期')
        saved={i['id']:i for i in c.execute('SELECT * FROM plan_items WHERE plan_id=? AND plan_version=?',(raw['id'],previous['version']))}
        incoming={i.get('id'):i for i in items}
        for identifier,old in saved.items():
            if old['status']=='COMPLETED':
                require(identifier in incoming and canonical(incoming[identifier])==canonical(json.loads(old['data'])),
                        '重排不得删除、移动或改写已完成任务')
    ranges=[];reviews=set();ids=set();completed_minutes=0;remaining_minutes=0
    for item in items:
        for key in ('id','knowledge_id','start','end','goal','reason','acceptance'): text(item.get(key),key)
        require(item['id'] not in ids,'计划内任务ID重复');ids.add(item['id'])
        point=c.execute('SELECT 1 FROM knowledge_points WHERE id=?',(item['knowledge_id'],)).fetchone()
        require(point,'无效知识点','NOT_FOUND')
        a,b=minute(item['start']),minute(item['end']);require(a<b,'计划时间非法')
        existing=saved.get(item['id'])
        status=(status_overrides or {}).get(item['id'],existing['status'] if existing else 'PENDING')
        if status=='COMPLETED':
            completed_minutes+=b-a
            ranges.append((a,b))
        elif status in ('PENDING','ACTIVE'):
            require(not spec['rest'] and any(a>=x and b<=y for x,y in windows) and not any(a<y and b>x for x,y in blocked),'计划侵占不可用时段','SCHEDULE_CONFLICT')
            remaining_minutes+=b-a
            ranges.append((a,b))
        rid=item.get('review_id')
        if rid:
            require(rid not in reviews,'重复绑定复习任务','DUPLICATE_REVIEW');reviews.add(rid)
            validate_review_binding(c,raw['id'],item,status,existing)
    ranges.sort()
    require(all(ranges[i][1]<=ranges[i+1][0] for i in range(len(ranges)-1)),'计划任务重叠','SCHEDULE_CONFLICT')
    require(remaining_minutes<=max(0,spec['max_minutes']-completed_minutes),'超过每日剩余上限','SCHEDULE_CONFLICT')
    return h


def apply_plan(store,raw):
    control=store.config('materials',store.c).get('learning_control',{})
    if control.get('daily_planning')=='USER_ONLY' and raw.get('items'):
        require(raw.get('source')=='user_provided' and bool(raw.get('authorization_ref')),
                '每日内容与数量由用户决定，只能登记用户明确提供的安排','DAILY_PLANNING_USER_ONLY')
    validate_plan(store,store.c,raw)
    other=store.c.execute('SELECT id FROM daily_plans WHERE day=? AND id<>? LIMIT 1',(raw['day'],raw['id'])).fetchone()
    require(not other,'该日已有计划，请修订原计划，避免跨计划超容量','PLAN_CONFLICT')
    previous=store.c.execute('SELECT * FROM daily_plans WHERE id=? ORDER BY version DESC LIMIT 1',(raw['id'],)).fetchone()
    version=(previous['version'] if previous else 0)+1
    require(raw.get('expected_revision',0)==version-1,'计划版本冲突','STALE_REVISION')
    if previous:
        require(previous['day']==raw['day'],'修订不能变更计划所属日期')
    store.c.execute('INSERT INTO daily_plans VALUES(?,?,?,?,?)',(raw['id'],version,raw['day'],raw['schedule_hash'],canonical(raw)))
    for i in raw['items']:
        status='PENDING'
        if previous:
            old=store.c.execute('SELECT status FROM plan_items WHERE plan_id=? AND plan_version=? AND id=?',(raw['id'],version-1,i['id'])).fetchone()
            if old: status=old[0]
        store.c.execute('INSERT INTO plan_items VALUES(?,?,?,?,?,?,?,?,?)',(i['id'],raw['id'],version,i['knowledge_id'],i.get('review_id'),i['start'],i['end'],status,canonical(i)))
    store.event('plan',raw['id'],'revised' if previous else 'created',{'version':version,'plan':raw},raw.get('expected_revision') if previous else None)
    return {'id':raw['id'],'version':version,'items':len(raw['items'])}
