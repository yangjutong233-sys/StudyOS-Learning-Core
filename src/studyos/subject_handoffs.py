"""Subject snapshots share the root report contract but isolate progress and evidence."""
import json
from .common import canonical, require, safe_path
from .storage import rows


def validate_scopes(store,config,*,unique=True):
    scopes=config.get('handoff_scopes',[])
    require(isinstance(scopes,list),'handoff_scopes必须为数组')
    ids=set(); paths=set()
    for scope in scopes:
        require(isinstance(scope,dict),'科目交接配置必须为对象')
        identifier=scope.get('id','')
        require(isinstance(identifier,str) and identifier and all(x.isalnum() or x in '-_' for x in identifier),'交接范围ID无效')
        require(scope.get('subject') in config['subjects'],'交接科目无效')
        require(isinstance(scope.get('title'),str) and scope['title'],'交接标题不能为空')
        require(scope.get('textbook') is None or (isinstance(scope.get('textbook'),str) and scope['textbook'].strip()),
                '教材名称必须为非空字符串')
        require(isinstance(scope.get('directory'),str) and scope['directory'],'交接目录不能为空')
        path=safe_path(store.root,scope['directory'])
        require(path!=store.root and identifier not in ids and path not in paths,'交接范围或目录重复')
        require(isinstance(scope.get('prefix',''),str),'知识点前缀无效')
        require(scope.get('catalog_id') is None or isinstance(scope['catalog_id'],str),'教材范围无效')
        require(scope.get('material_prefix') is None or isinstance(scope['material_prefix'],str),'材料前缀无效')
        require(scope.get('material_keys') is None or (isinstance(scope['material_keys'],list) and all(isinstance(k,str) for k in scope['material_keys'])),'材料键范围无效')
        names=scope.get('files',{'codex':'CODEX_HANDOFF.md','gpt':'GPT_TEACHING_HANDOFF.md'})
        require(isinstance(names,dict) and set(names)=={'codex','gpt'} and
                all(isinstance(n,str) and n.endswith('.md') and '/' not in n and '\\' not in n for n in names.values()),'交接文件名无效')
        if unique:
            require(names=={'codex':'CODEX_HANDOFF.md','gpt':'GPT_TEACHING_HANDOFF.md'},'每科只保留标准命名的一对交接，不生成编号副本')
        for other in scopes[:len(ids)] if unique else []:
            same_catalog=(not scope.get('catalog_id') or not other.get('catalog_id') or scope['catalog_id']==other['catalog_id'])
            a,b=scope.get('prefix',''),other.get('prefix','')
            require(not (scope['subject']==other['subject'] and same_catalog and (a.startswith(b) or b.startswith(a))),
                    '科目交接范围重叠；408只配置四个具体科目，不再配置408总览')
        ids.add(identifier);paths.add(path)
    return scopes


def effective_scopes(store,config):
    """Render old backup configurations without rewriting their saved state."""
    scopes=validate_scopes(store,config,unique=False)
    def covers(a,b):
        return (a['subject']==b['subject'] and
                (not a.get('catalog_id') or a.get('catalog_id')==b.get('catalog_id')) and
                b.get('prefix','').startswith(a.get('prefix','')))
    kept=[]
    for index,scope in enumerate(scopes):
        # Prefer concrete subjects over their aggregate; equivalent scopes use the
        # shallowest directory, then configuration order (e.g. English vs 05/06).
        rank=(len(safe_path(store.root,scope['directory']).parts),index)
        if any(covers(scope,other) and
               (not covers(other,scope) or (len(safe_path(store.root,other['directory']).parts),i)<rank)
               for i,other in enumerate(scopes) if i!=index):
            continue
        kept.append({**scope,'files':{'codex':'CODEX_HANDOFF.md','gpt':'GPT_TEACHING_HANDOFF.md'}})
    return validate_scopes(store,{**config,'handoff_scopes':kept})


def documents(store,c,meta,config):
    from .handoff import bodies
    from .materials import resolve_file
    output=[]
    selected=c.execute("SELECT value FROM meta WHERE key='active_task'").fetchone()
    selected_id=selected[0] if selected else None
    for scope in effective_scopes(store,config['materials']):
        points=rows(c,"""SELECT k.*,cat.subject,cat.name AS catalog_name FROM knowledge_points k
            JOIN catalogs cat ON cat.id=k.catalog_id WHERE cat.subject=?
            AND (? IS NULL OR k.catalog_id=?) AND substr(k.external_id,1,length(?))=?
            ORDER BY k.catalog_id,COALESCE(json_extract(k.data,'$.source_order'),-1),k.external_id""",
            (scope['subject'],scope.get('catalog_id'),scope.get('catalog_id'),scope.get('prefix',''),scope.get('prefix','')))
        ids=[p['id'] for p in points];marks=','.join('?' for _ in ids) or 'NULL'
        task=None;k=None
        # Open sessions precede the selected task; creation order is only a fallback.
        # Withdrawn proposals are never resumed as a lesson.
        candidate=c.execute(f"""SELECT t.* FROM lesson_tasks t WHERE t.knowledge_id IN ({marks})
            AND t.version=(SELECT MAX(t2.version) FROM lesson_tasks t2 WHERE t2.id=t.id)
            AND (EXISTS (SELECT 1 FROM learning_sessions s WHERE s.task_id=t.id AND s.status IN ('ACTIVE','PAUSED'))
                 OR COALESCE(json_extract(t.data,'$.arrangement_status'),'')<>'WITHDRAWN_BY_USER')
            ORDER BY EXISTS (SELECT 1 FROM learning_sessions s WHERE s.task_id=t.id AND s.status IN ('ACTIVE','PAUSED')) DESC,
            (t.id=?) DESC, t.rowid DESC LIMIT 1""",[*ids,selected_id]).fetchone()
        if candidate:
            task=dict(candidate);task['data']=json.loads(task['data'])
            k=next(dict(p) for p in points if p['id']==task['knowledge_id']);k['data']=json.loads(k['data'])
        smeta={**meta,'scope_id':scope['id'],'subject':scope['subject'],
               'task_id':task['id'] if task else None,'task_version':task['version'] if task else None}
        counts={}
        for p in points:counts[p['status']]=counts.get(p['status'],0)+1
        sessions=c.execute(f'SELECT COUNT(*) FROM learning_sessions s JOIN lesson_tasks t ON t.id=s.task_id AND t.version=s.task_version WHERE t.knowledge_id IN ({marks})',ids).fetchone()[0]
        attempts=c.execute(f'SELECT COUNT(*) FROM attempts a JOIN questions q ON q.id=a.question_id AND q.version=a.question_version WHERE q.knowledge_id IN ({marks})',ids).fetchone()[0]
        first=next((p for p in points if not json.loads(p['data']).get('navigation_only')),None)
        navigation=[]
        spec=config['materials']['subjects'][scope['subject']]
        for key,item in spec.get('files',{}).items():
            if scope.get('material_keys') is not None and key not in scope['material_keys']:continue
            if scope.get('material_prefix') is not None and not key.startswith(scope['material_prefix']):continue
            if item.get('role') in ('catalog','outline','dependencies','index'):
                navigation.append({'角色':item['role'],'路径':str(resolve_file(store,scope['subject'],key,config['materials']))})
        summary={'范围':scope['title'],'知识条目总数':len(points),'状态分布':counts,'真实学习会话数':sessions,'真实作答数':attempts,
                 '学习状态':'尚未实际学习；从0开始，能力未评估' if sessions==0 and attempts==0 else '以本范围真实记录及下列任务证据为准',
                 '当前任务':task['id'] if task else None,
                 '起始导航（不是已安排任务）':{x:first[x] for x in ('external_id','name')} if first else None,
                 '资料状态':'已配置' if spec.get('path') is not None else '未配置；等待用户提供资料',
                 '安排方式':'由用户自行选择学习内容和时间；作废计划不恢复，不自动开课。'}
        self_directed=config['materials'].get('learning_control',{}).get('daily_planning')=='USER_ONLY'
        if self_directed:
            summary.pop('起始导航（不是已安排任务）')
            summary['安排方式']='每日内容、数量、顺序与停止位置由用户决定；程序只按真实回传更新进度。'
        received=c.execute(f"SELECT COUNT(*) FROM artifacts WHERE kind='reports' AND status='PENDING' AND json_extract(data,'$.intake_only')=1 AND json_extract(data,'$.knowledge_id') IN ({marks})",ids).fetchone()[0]
        if received:
            summary['待核验教学回传数']=received
            if sessions==0 and attempts==0:summary['学习状态']='已收到初步学习的教学回传；正式会话与逐题证据待补，掌握未评估。'
        confirmed=c.execute(f"SELECT COUNT(*) FROM artifacts WHERE kind='reports' AND status='ACCEPTED' AND json_extract(data,'$.evidence_status')='USER_CONFIRMED_LEARNING' AND json_extract(data,'$.knowledge_id') IN ({marks})",ids).fetchone()[0]
        if confirmed:
            summary['正式学习回传数']=confirmed
            summary['学习状态']='已正式保存用户交付的真实学习记录；独立掌握按后续测试判定。'
        codex,gpt=bodies(store,c,smeta,task,k,config,ids)
        introduction='## '+scope['title']+'：实际学习进度\n\n'+canonical(summary)+'\n\n'
        nav='## 科目资料导航\n\n'+canonical(navigation)+'\n\n'
        rule=config['review_policy'].get('subject_overrides',{}).get(scope['subject']) or {'basis':'actual_completion_date','intervals':config['review_policy']['intervals']}
        names=scope.get('files',{'codex':'CODEX_HANDOFF.md','gpt':'GPT_TEACHING_HANDOFF.md'})
        delivery=('## 网页版上传入口\n\n'+str(safe_path(store.root,scope['directory'])/names['gpt'])+
                  '\n\n上传本次相关Markdown课件、教材或习题原件的必要页，以及本目录教学交接。'
                  '课件按下列任务定位取必要范围，保留原ID和版本；每科仅保留本目录的一对标准交接。'
                  '交付前核对本文件头部task_id和task_version与实际选定任务一致。\n\n')
        shared=introduction+delivery+nav+'## 本科复习规则\n\n'+canonical(rule)+'\n\n'
        if not task:
            shared+='## 任务与附件边界\n\n'+('当前没有已关联的正式教学会话；用户可自主学习并回传成果，缺少真实关联先保留待核验，不补造会话或计划。' if self_directed else '尚无有效教学任务。先由用户选定知识点，再由Codex保存目标、策略、标准及材料版本并重建交接。')+'未附教材页不声称已读；不上传全教材，不以目录或旧样例推定学习进度。\n\n'
        output.append({'id':scope['id'],'directory':safe_path(store.root,scope['directory']),
                       'files':scope.get('files',{'codex':'CODEX_HANDOFF.md','gpt':'GPT_TEACHING_HANDOFF.md'}),
                       'metadata':smeta,
                       'codex':'# '+scope['title']+' · Codex 工作交接\n\n'+shared+codex.replace('# Codex 最新工作交接','## 工作交接',1),
                       'gpt':('# '+scope['title']+'学习进度\n\n'+gpt if self_directed else
                              '# '+scope['title']+' · GPT 网页教学交接\n\n'+shared+gpt.replace('# GPT 网页教学交接','## 教学交接',1))})
    return output
