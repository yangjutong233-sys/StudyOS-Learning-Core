from pathlib import Path
import json
from .common import SUBJECTS, canonical, day, digest, number, require, sha, text, uid
from .storage import one, rows


def text_ranges(locator, length):
    ranges=locator.get('line_ranges',[[locator.get('line_start'),locator.get('line_end',locator.get('line_start'))]])
    require(isinstance(ranges,list) and bool(ranges),'文本需要非空行号范围')
    previous=0
    for pair in ranges:
        require(isinstance(pair,list) and len(pair)==2,'行号范围需要起止行')
        a,z=pair
        require(type(a) is int and type(z) is int and previous<a<=z<=length,'文本定位超出范围或重叠')
        previous=z
    return ranges


def resolve_file(store,subject,key,config=None):
    config=config or store.config('materials')
    require(subject in SUBJECTS,'未知科目')
    spec=config['subjects'].get(subject,{})
    require(spec.get('path') is not None,'科目材料尚未配置','MATERIAL_UNCONFIGURED',subject)
    item=spec.get('files',{}).get(key)
    require(isinstance(item,dict) and isinstance(item.get('path'),str),'材料路径键未配置','MATERIAL_UNCONFIGURED',key)
    base=Path(config.get('root') or store.root)
    if not base.is_absolute(): base=store.root/base
    subject_path=Path(spec['path'])
    if not subject_path.is_absolute(): subject_path=base/subject_path
    p=Path(item['path'])
    return (p if p.is_absolute() else subject_path/p).resolve()


def resolve_binding(store,c,binding,config=None):
    """Current versions follow explicit configuration; history uses its registered path."""
    material=c.execute('SELECT * FROM materials WHERE id=? AND version=?',
                       (binding['material_id'],binding['material_version'])).fetchone()
    require(material,'材料版本不存在','NOT_FOUND')
    latest=c.execute('SELECT MAX(version) FROM materials WHERE id=?',(binding['material_id'],)).fetchone()[0]
    saved=json.loads(material['data']).get('registered_path')
    if binding['material_version']<latest and saved:
        return Path(saved)
    # Legacy registrations have no saved path. Only the configured file with the exact
    # registered hash is usable; callers reject a mismatch, never search or substitute.
    return resolve_file(store,material['subject'],material['path_key'],config)


def knowledge(store,raw):
    catalog=raw.get('catalog',{})
    text(catalog.get('id'),'catalog.id');text(catalog.get('name'),'catalog.name')
    require(catalog.get('subject') in SUBJECTS,'未知科目')
    old=store.c.execute('SELECT * FROM catalogs WHERE id=?',(catalog['id'],)).fetchone()
    if old:
        require(old['subject']==catalog['subject'] and old['name']==catalog['name'],'教材ID已绑定不同内容','ID_CONFLICT')
    else:
        store.c.execute('INSERT INTO catalogs VALUES(?,?,?)',(catalog['id'],catalog['subject'],catalog['name']))
        store.event('catalog',catalog['id'],'created',catalog)
    points=raw.get('points');require(isinstance(points,list) and points,'points 必须为非空数组')
    result=[]
    for p in points:
        external=text(p.get('external_id'),'external_id');name=text(p.get('name'),'name')
        text(p.get('criteria'),'criteria')
        require(not store.c.execute('SELECT 1 FROM knowledge_points WHERE catalog_id=? AND external_id=?',(catalog['id'],external)).fetchone(),'知识点已存在，不覆盖原档案','ID_CONFLICT')
        identifier=p.get('id') or uid()
        state=p.get('initial_state','UNASSESSED')
        require(state in ('UNASSESSED','UNLEARNED'),'导入不能授予掌握')
        if state=='UNLEARNED': text(p.get('user_statement'),'确认未学习的原话')
        store.c.execute('INSERT INTO knowledge_points(id,catalog_id,external_id,name,status,data) VALUES(?,?,?,?,?,?)',
                        (identifier,catalog['id'],external,name,state,canonical(p)))
        store.event('knowledge',identifier,'created',p)
        result.append({'id':identifier,'external_id':external})
    for p,r in zip(points,result):
        for kind in ('strong','weak'):
            for ext in p.get(kind,[]):
                q=store.c.execute('SELECT id FROM knowledge_points WHERE catalog_id=? AND external_id=?',(catalog['id'],ext)).fetchone()
                require(q is not None,'前置知识点不存在','INVALID_DEPENDENCY',ext)
                store.c.execute('INSERT INTO knowledge_dependencies VALUES(?,?,?)',(r['id'],q[0],kind))
    edges={}
    for r in store.c.execute("SELECT * FROM knowledge_dependencies WHERE kind='strong'"):
        edges.setdefault(r['knowledge_id'],[]).append(r['prerequisite_id'])
    def visit(n,path):
        require(n not in path,'强依赖形成环','INVALID_DEPENDENCY')
        for other in edges.get(n,[]): visit(other,path|{n})
    for n in edges: visit(n,set())
    return {'points':result,'catalog':catalog}


def register(store,raw):
    subject=raw.get('subject');key=text(raw.get('path_key'),'path_key')
    p=resolve_file(store,subject,key,store.config('materials',store.c))
    require(p.is_file(),'材料路径不存在','MATERIAL_MISSING',str(p))
    role=raw.get('role','outline')
    require(role in ('catalog','outline','dependencies','index','textbook','exercises','answer','reserved_answer'),'未知材料角色')
    mid=text(raw.get('id'),'id')
    latest=store.c.execute('SELECT MAX(version) FROM materials WHERE id=?',(mid,)).fetchone()[0] or 0
    require(raw.get('expected_revision',0)==latest,'材料版本冲突','STALE_REVISION')
    version=latest+1
    store.c.execute('INSERT INTO materials VALUES(?,?,?,?,?,?,?)',(mid,version,subject,role,key,sha(p),canonical({**raw,'registered_path':str(p)})))
    for b in raw.get('bindings',[]):
        point=one(store.c,'knowledge_points',b['knowledge_id'])
        cat=one(store.c,'catalogs',point['catalog_id'])
        require(cat['subject']==subject,'跨科材料绑定无效')
        locator=b.get('locator',{})
        if p.suffix.lower()=='.pdf':
            a=locator.get('page_start');z=locator.get('page_end',a)
            require(type(a) is int and type(z) is int and 1<=a<=z,'PDF需要物理页码范围')
        else:
            text_ranges(locator,len(p.read_text(encoding='utf-8-sig').splitlines()))
        store.c.execute('INSERT INTO knowledge_materials VALUES(?,?,?,?)',(point['id'],mid,version,canonical(locator)))
    store.event('material',mid,'version_registered',{'version':version,'sha256':sha(p),'path_key':key})
    return {'id':mid,'version':version,'sha256':sha(p)}


def check(store,c,subject=None):
    config=store.config('materials',c);out=[]
    for s,spec in config['subjects'].items():
        if subject and subject!=s: continue
        if spec.get('path') is None:
            out.append({'subject':s,'status':'MATERIAL_UNCONFIGURED'});continue
        for key in spec.get('files',{}):
            p=resolve_file(store,s,key,config)
            registered=c.execute('SELECT sha256 FROM materials WHERE subject=? AND path_key=? ORDER BY version DESC LIMIT 1',(s,key)).fetchone()
            state='UNREGISTERED'
            if not p.is_file(): state='MATERIAL_MISSING'
            elif registered: state='READY' if registered[0]==sha(p) else 'MATERIAL_CHANGED'
            out.append({'subject':s,'path_key':key,'path':str(p),'status':state})
    return out


def locate(store,c,knowledge_id,level=1,reason=None,read=False,material_id=None,material_version=None,task_id=None,task_version=None):
    require(type(level) is int and 1<=level<=4,'level 必须为1到4')
    if level>1: text(reason,'扩大读取范围的 reason')
    pinned=None
    if task_id:
        if task_version is None:
            task=c.execute('SELECT * FROM lesson_tasks WHERE id=? ORDER BY version DESC LIMIT 1',(task_id,)).fetchone()
        else:task=c.execute('SELECT * FROM lesson_tasks WHERE id=? AND version=?',(task_id,task_version)).fetchone()
        require(task,'教学任务版本不存在','NOT_FOUND')
        require(not knowledge_id or task['knowledge_id']==knowledge_id,'任务与知识点不符','CONTEXT_SCOPE')
        knowledge_id=task['knowledge_id'];pinned=json.loads(task['data']).get('material_bindings',[])
        require(level!=4,'任务锁定材料仅用于当前知识点；前置请单独按知识点读取','CONTEXT_SCOPE')
    require(material_version is None or material_id,'指定材料版本时必须提供material_id')
    require(task_version is None or task_id,'指定任务版本时必须提供task_id')
    k=one(c,'knowledge_points',knowledge_id);cat=one(c,'catalogs',k['catalog_id'])
    roles={1:('catalog',),2:('outline','dependencies'),3:('index','textbook','exercises'),4:('outline','dependencies')}[level]
    targets=[knowledge_id]
    if level==4:
        targets=[r['prerequisite_id'] for r in c.execute('SELECT prerequisite_id FROM knowledge_dependencies WHERE knowledge_id=?',(knowledge_id,))]
    result=[]
    for target in targets:
        bindings=rows(c,'SELECT km.*,m.subject,m.path_key,m.sha256,m.role FROM knowledge_materials km JOIN materials m ON m.id=km.material_id AND m.version=km.material_version WHERE km.knowledge_id=?',(target,))
        if pinned is not None:
            bindings=[b for b in bindings if {'material_id':b['material_id'],'material_version':b['material_version']} in pinned]
        elif material_version is None:
            latest={}
            for b in bindings:latest[b['material_id']]=max(latest.get(b['material_id'],0),b['material_version'])
            bindings=[b for b in bindings if b['material_version']==latest[b['material_id']]]
        if material_id:bindings=[b for b in bindings if b['material_id']==material_id]
        if material_version is not None:bindings=[b for b in bindings if b['material_version']==material_version]
        if material_id or material_version is not None:
            require(bindings,'所选材料版本未绑定当前知识点或任务','NOT_FOUND')
        for b in bindings:
            if b['role'] not in roles: continue
            p=resolve_binding(store,c,b,store.config('materials',c))
            require(p.is_file(),'材料不存在','MATERIAL_MISSING',str(p))
            require(sha(p)==b['sha256'],'材料版本变化，需显式登记','MATERIAL_CHANGED',str(p))
            loc=json.loads(b['locator']);item={'knowledge_id':target,'material_id':b['material_id'],'version':b['material_version'],
                 'path':str(p),'role':b['role'],'locator':loc}
            if read:
                if p.suffix.lower()=='.pdf':
                    item['instruction']='仅按locator物理页核对原PDF；本程序不声称已经理解PDF内容。'
                else:
                    lines=p.read_text(encoding='utf-8-sig').splitlines()
                    ranges=text_ranges(loc,len(lines))
                    content='\n\n'.join('\n'.join(lines[a-1:z]) for a,z in ranges);require(len(content)<=24000,'摘录过长，请缩小定位','READ_SCOPE_TOO_LARGE')
                    item['text']=content
            result.append(item)
    return {'knowledge_id':knowledge_id,'subject':cat['subject'],'external_id':k['external_id'],'level':level,
            'reason':reason,'materials':result,'next':'只在当前任务需要时展开下一层'}
