from pathlib import Path
from contextlib import closing
import json
import shutil
import sqlite3
import time
from .common import Error, atomic, canonical, digest, load, now, operation_lock, require, safe_path, sha, text, uid
from .storage import SCHEMA_VERSION, Store, defaults, rows


def counts(c):
    return {r[0]:c.execute('SELECT COUNT(*) FROM "'+r[0]+'"').fetchone()[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}


def doctor(store,c):
    issues=[]
    integrity=[r[0] for r in c.execute('PRAGMA integrity_check')]
    if integrity!=['ok']: issues.append({'code':'INTEGRITY','details':integrity})
    foreign=rows(c,'PRAGMA foreign_key_check')
    if foreign: issues.append({'code':'FOREIGN_KEYS','details':foreign})
    duplicate_reviews=rows(c,"""SELECT i.review_id,COUNT(*) AS bindings FROM plan_items i
        WHERE i.review_id IS NOT NULL AND i.status IN ('PENDING','ACTIVE')
        AND i.plan_version=(SELECT MAX(p.version) FROM daily_plans p WHERE p.id=i.plan_id)
        GROUP BY i.review_id HAVING COUNT(*)>1""")
    if duplicate_reviews:issues.append({'code':'DUPLICATE_REVIEW_BINDING','details':duplicate_reviews})
    if (store.root/'database/config-journal.json').exists(): issues.append({'code':'RECOVERY_REQUIRED'})
    schema=Path(__file__).with_name('schema.sql').read_text(encoding='utf-8').encode('utf-8')
    version=c.execute('SELECT sha256 FROM schema_migrations WHERE version=?',(SCHEMA_VERSION,)).fetchone()
    if not version or version[0]!=digest(schema): issues.append({'code':'MIGRATION_CHECKSUM'})
    for name in defaults():
        try: store.config(name,c)
        except (Error,OSError) as e: issues.append({'code':'CONFIG','name':name,'message':str(e)})
    referenced=set()
    for a in c.execute('SELECT * FROM artifacts'):
        try:
            p=safe_path(store.root,a['path']);referenced.add(p)
            if not p.is_file() or sha(p)!=a['sha256']: issues.append({'code':'ARTIFACT_HASH','id':a['id'],'path':a['path']})
        except Error as e: issues.append({'code':e.code,'id':a['id']})
    objects={'knowledge':'knowledge_points','catalog':'catalogs','material':'materials','lesson':'lesson_tasks',
             'selection':'lesson_tasks','session':'learning_sessions','attempt':'attempts','evaluation':'attempt_evaluations',
             'assessment':'assessments','mastery':'mastery_assessments','mistake':'mistakes','review_series':'review_series',
             'review_task':'review_tasks','plan':'daily_plans','plan_item':'plan_items','report':'artifacts','migration':'artifacts','schedule_exception':'schedule_exceptions'}
    revisions={}
    for e in c.execute('SELECT * FROM audit_events ORDER BY event_seq'):
        key=(e['entity_type'],e['entity_id']);previous=revisions.get(key)
        if e['previous_revision']!=previous or e['new_revision']!=(previous or 0)+1:
            issues.append({'code':'AUDIT_REVISION','event_id':e['event_id']})
        revisions[key]=e['new_revision']
        if e['payload_version']!=1: issues.append({'code':'PAYLOAD_VERSION','event_id':e['event_id']})
        table=objects.get(e['entity_type'])
        if not table and e['entity_type']!='config': issues.append({'code':'UNKNOWN_ENTITY_TYPE','event_id':e['event_id']})
        if table and not c.execute(f'SELECT 1 FROM {table} WHERE id=?',(e['entity_id'],)).fetchone():
            issues.append({'code':'AUDIT_REFERENCE','event_id':e['event_id']})
    for m in c.execute('SELECT data FROM mastery_assessments'):
        for identifier in json.loads(m[0]).get('attempt_ids',[]):
            if not c.execute('SELECT 1 FROM attempts WHERE id=?',(identifier,)).fetchone(): issues.append({'code':'EVIDENCE_REFERENCE','id':identifier})
    for s in c.execute("SELECT data FROM review_series WHERE kind='comprehensive'"):
        for identifier in json.loads(s[0]).get('members',[]):
            if not c.execute('SELECT 1 FROM knowledge_points WHERE id=?',(identifier,)).fetchone(): issues.append({'code':'REVIEW_REFERENCE','id':identifier})
    for t in c.execute('SELECT data FROM lesson_tasks'):
        for b in json.loads(t[0]).get('material_bindings',[]):
            if not c.execute('SELECT 1 FROM materials WHERE id=? AND version=?',(b['material_id'],b['material_version'])).fetchone():
                issues.append({'code':'TASK_MATERIAL_REFERENCE','id':b['material_id']})
    orphans=[]
    for folder in ('config','reports','report_data'):
        for p in (store.root/'records'/folder).rglob('*'):
            if p.is_file() and p.resolve() not in referenced: orphans.append(str(p.relative_to(store.root)))
    return {'ok':not issues,'issues':issues,'orphan_artifacts':orphans,'counts':counts(c)}


def verify_backup(path):
    path=Path(path).resolve();manifest=load(path/'manifest.json')
    require(manifest.get('format_version')==1,'备份格式不支持','BACKUP_INVALID')
    for relative,expected in manifest['files'].items():
        p=safe_path(path,relative)
        require(p.is_file() and sha(p)==expected,'备份文件缺失或损坏','BACKUP_CORRUPT',relative)
    p=path/'snapshot.sqlite3'
    require(p.is_file(),'备份数据库缺失','BACKUP_CORRUPT')
    with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)) as c:
        require(c.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'备份数据库完整性失败','BACKUP_CORRUPT')
        require(not c.execute('PRAGMA foreign_key_check').fetchall(),'备份外键失败','BACKUP_CORRUPT')
        require(counts(c)==manifest['counts'],'备份数量不匹配','BACKUP_CORRUPT')
        require(c.execute('SELECT value FROM meta WHERE key=?',('database_id',)).fetchone()[0]==manifest['database_id'],'备份数据库身份不匹配','BACKUP_CORRUPT')
    return manifest


def restore_backup(path,destination,active_root=None):
    source=Path(path).resolve();dest=Path(destination).resolve()
    require(dest!=source and not dest.is_relative_to(source) and not source.is_relative_to(dest),'恢复目录不能包含备份源','INVALID_PATH')
    if active_root:
        active=Path(active_root).resolve()
        require(dest!=active and not dest.is_relative_to(active) and not active.is_relative_to(dest),'只能恢复到活动项目之外的新目录','INVALID_PATH')
    require(not dest.exists(),'恢复目标必须为不存在的新目录','TARGET_EXISTS')
    m=verify_backup(source)
    dest.mkdir(parents=True)
    try:
        (dest/'database').mkdir();shutil.copyfile(source/'snapshot.sqlite3',dest/'database/studyos.sqlite3')
        for relative in m['files']:
            if relative.startswith('files/'):
                target=safe_path(dest,relative[6:]);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/relative,target)
            elif relative.startswith('external/'):
                target=safe_path(dest,relative);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/relative,target)
        restored=Store(dest)
        with restored.connect(True) as c:
            result=doctor(restored,c)
            require(result['ok'],'恢复后档案校验失败','RESTORE_FAILED',result)
            require(result['counts']==m['counts'],'恢复后数量变化','RESTORE_FAILED')
        from .handoff import handoff
        derived=handoff(restored)
        report={'status':'RESTORE_VERIFIED','destination':str(dest),'counts':m['counts'],
                'database_id':m['database_id'],'handoff':derived,'material_paths':m.get('external_mapping',[]),
                'note':'外部教材原路径保持不变；路径失效时使用清单显式 materials update，不自动搜索或切换正式数据源。'}
        atomic(dest/'RESTORE_REPORT.json',canonical(report));return report
    except BaseException as e:
        atomic(dest/'RESTORE_FAILURE.json',canonical({'status':'FAILED','message':str(e)}))
        raise


def create_backup(store,mode='personal'):
    require(mode in ('personal','complete'),'备份模式无效')
    require(store.path.is_file(),'数据库未初始化','NOT_INITIALIZED')
    identifier=uid();temporary=store.root/'backups'/('.partial-'+identifier)
    temporary.mkdir(parents=True,exist_ok=False)
    files={};mapping=[]
    try:
        with operation_lock(store.root):
            store.guard()
            with store.connect(True) as source:
                with closing(sqlite3.connect(temporary/'snapshot.sqlite3')) as target:
                    started=time.monotonic()
                    def progress(*_):
                        if time.monotonic()-started>30: raise Error('BACKUP_TIMEOUT','一致性备份超时')
                    source.backup(target,pages=128,progress=progress,sleep=.05)
            snapshot=sqlite3.connect(temporary/'snapshot.sqlite3');snapshot.row_factory=sqlite3.Row
            try:
                state=doctor(store,snapshot)
                require(state['ok'],'活动档案不完整，不能宣称完整备份','BACKUP_INCOMPLETE',state)
                for name in defaults():
                    relative=f'files/config/{name}.json';p=temporary/relative;p.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(store.root/f'config/{name}.json',p);files[relative]=sha(p)
                artifacts=rows(snapshot,'SELECT path,sha256 FROM artifacts')
                registered=rows(snapshot,'SELECT * FROM materials') if mode=='complete' else []
                from .materials import resolve_binding
                material_config=load(temporary/'files/config/materials.json')
                for material in registered:
                    material['resolved_path']=str(resolve_binding(store,snapshot,
                        {'material_id':material['id'],'material_version':material['version']},material_config))
                count_snapshot=counts(snapshot)
                database_id=snapshot.execute("SELECT value FROM meta WHERE key='database_id'").fetchone()[0]
                event_seq=snapshot.execute('SELECT COALESCE(MAX(event_seq),0) FROM audit_events').fetchone()[0]
            finally: snapshot.close()
        # Immutable artifacts remain stable after releasing the operation lock.
        for a in artifacts:
            source=safe_path(store.root,a['path']);relative='files/'+a['path'];p=safe_path(temporary,relative)
            p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,p)
            require(sha(p)==a['sha256'],'档案复制时内容变化','BACKUP_INCOMPLETE',a['path']);files[relative]=a['sha256']
        for r in registered:
            source=Path(r['resolved_path'])
            require(source.is_file() and sha(source)==r['sha256'],'教材缺失或变化，完整备份失败','BACKUP_INCOMPLETE',str(source))
            relative='external/'+r['sha256']+'/'+source.name;p=safe_path(temporary,relative);p.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,p);require(sha(p)==r['sha256'],'教材复制时变化','BACKUP_INCOMPLETE')
            files[relative]=r['sha256'];mapping.append({'original':str(source),'backup_path':relative,'sha256':r['sha256'],
                                                     'material_id':r['id'],'material_version':r['version']})
        files['snapshot.sqlite3']=sha(temporary/'snapshot.sqlite3')
        manifest={'format_version':1,'id':identifier,'mode':mode,'created_at':now(),'database_id':database_id,
                  'source_event_seq':event_seq,'counts':count_snapshot,'files':files,'external_mapping':mapping,
                  'external_dependencies':mode=='personal','handoffs':'rebuild_from_database'}
        atomic(temporary/'manifest.json',canonical(manifest));verify_backup(temporary)
        # An actual independent-directory restore is part of every successful backup.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='studyos-restore-') as temp:
            restore_backup(temporary,Path(temp)/'restored')
        atomic(temporary/'VERIFIED.json',canonical({'restore_verified':True,'at':now()}))
        published=store.root/'backups'/identifier;temporary.rename(published)
        return {'status':'BACKUP_VERIFIED','path':str(published),'mode':mode,'files':len(files),'restore_verified':True}
    except BaseException as e:
        atomic(temporary/'FAILURE.json',canonical({'status':'FAILED','message':str(e)}))
        raise
