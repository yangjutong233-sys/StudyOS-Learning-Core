"""Read-only legacy inspection and explicitly authorized config-only migration."""
from pathlib import Path
import tempfile
from .common import canonical, digest, load, operation_lock, require, sha, text, uid
from .storage import Store, rows


def inspect(source):
    root=Path(source).resolve();base=root/'study_data/tracks/legacy_exam'
    require((base/'config.json').is_file(),'未找到考研配置','MIGRATION_SOURCE_INVALID')
    selected=list(base.rglob('*.json'))
    selected.extend(base.rglob('*.md'))
    profile=root/'study_data/global/learner_model.json'
    if profile.is_file(): selected.append(profile)
    events=list((base/'events').glob('*.json'))
    state=load(base/'state.json') if (base/'state.json').exists() else {}
    # Refuse unsupported evidence rather than importing incomplete or fabricated history.
    has_evidence=bool(events or state.get('abilities') or state.get('review') or state.get('session'))
    files={p.relative_to(root).as_posix():sha(p) for p in selected}
    return {'source':str(root),'files':files,'mapping_version':1,'supported':not has_evidence,
            'scope':'legacy_exam configuration and general preferences; no grades',
            'excluded':['unrelated tracks','unmapped history','user code'],
            'evidence_requires_mapping':has_evidence,'fingerprint':digest(canonical(files).encode())}


def apply(store,raw):
    text(raw.get('authorization_ref'),'正式迁移授权 authorization_ref')
    info=inspect(raw.get('source'))
    require(info['supported'],'源中存在学习证据，必须先补充映射方案，拒绝不完整迁移','MIGRATION_MAPPING_REQUIRED')
    require(raw.get('source_fingerprint')==info['fingerprint'],'源文件变化，请重新检查','MIGRATION_SOURCE_CHANGED')
    source=Path(info['source'])
    require(source!=store.root and not source.is_relative_to(store.root) and not store.root.is_relative_to(source),'迁移源目标必须隔离','INVALID_PATH')
    existing=store.c.execute("SELECT id FROM artifacts WHERE kind='migration' AND entity_id=?",(info['fingerprint'],)).fetchone()
    if existing: return {'already_migrated':True,'artifact_id':existing[0]}
    config=load(source/'study_data/tracks/legacy_exam/config.json')
    preferences=source/'study_data/global/learner_model.json'
    profile=load(preferences) if preferences.is_file() else {}
    # Keep source context without falsely translating course abilities or activating a schedule.
    manifest={**info,'config':config,'preferences':{k:v for k,v in profile.items() if k in ('learning_style','preferred_explanation_style','attention_patterns','pace')},
              'mapping':[{'source':p,'sha256':h,'target':'immutable source archive'} for p,h in info['files'].items()],
              'schedule_remains_draft':store.config('schedule',store.c)['status']=='draft','authorization_ref':raw['authorization_ref']}
    for relative,expected in info['files'].items():
        p=source/relative
        require(sha(p)==expected,'迁移读取期间源变化','MIGRATION_SOURCE_CHANGED')
        store.artifact('migration_sources',info['fingerprint'],p.read_bytes(),suffix=p.suffix,data={'source':relative,'sha256':expected})
    identifier=store.artifact('migration',info['fingerprint'],canonical(manifest))
    store.c.execute("INSERT INTO meta VALUES('learner_preferences',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(manifest['preferences']),))
    store.c.execute("INSERT INTO meta VALUES('exam_goal',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(canonical(config.get('goal',{})),))
    store.event('migration',identifier,'config_imported',{'manifest_id':identifier,'source_fingerprint':info['fingerprint'],'authorization_ref':raw['authorization_ref']})
    return {'artifact_id':identifier,'learning_records_imported':0,'schedule_changed':False,'database_switched':False}


def dispatch(store,operation,raw):
    if operation=='migrate.inspect': return inspect(raw['source'])
    if operation=='migrate.dry-run':
        info=inspect(raw['source'])
        require(info['supported'],'需要先设计历史证据映射','MIGRATION_MAPPING_REQUIRED')
        with tempfile.TemporaryDirectory(prefix='studyos-migration-') as tmp:
            trial=Store(Path(tmp)/'trial');trial.initialize()
            result=trial.run('migrate.apply',{'request_id':uid(),'source':raw['source'],'source_fingerprint':info['fingerprint'],
                         'authorization_ref':'isolated synthetic trial only'},lambda r:apply(trial,r))
            from .maintenance import doctor
            with trial.connect(True) as c: checked=doctor(trial,c)
            require(checked['ok'],'试迁移完整性失败','MIGRATION_FAILED',checked)
            return {'trial':True,'source_fingerprint':info['fingerprint'],'result':result,'counts':checked['counts'],'formal_database_changed':False}
    if operation=='migrate.apply':
        text(raw.get('backup_path'),'迁移前已验证备份 backup_path')
        from .maintenance import verify_backup
        manifest=verify_backup(raw['backup_path'])
        with store.connect(True) as c:
            identity=c.execute("SELECT value FROM meta WHERE key='database_id'").fetchone()[0]
        require(manifest['database_id']==identity,'迁移备份不是目标数据库','BACKUP_INVALID')
        return store.run(operation,raw,lambda r:apply(store,r))
    if operation=='migrate.verify':
        with store.connect(True) as c:
            from .maintenance import doctor
            return {'doctor':doctor(store,c),'migrations':rows(c,"SELECT id,path,data FROM artifacts WHERE kind='migration'")}
    require(False,'未知迁移命令','UNKNOWN_COMMAND')
