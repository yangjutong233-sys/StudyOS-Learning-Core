from pathlib import Path
import json
import sqlite3
from .common import Error, atomic, canonical, digest, load, now, operation_lock, require, safe_path, sha, text, uid

SCHEMA_VERSION = 1


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def defaults():
    # No personal timetable is shipped. Users explicitly confirm their own windows.
    return {
        'schedule': {'schema_version':1,'revision':1,'timezone':'Asia/Shanghai','status':'draft',
                     'source':'Empty draft; configure and confirm your own available time',
                     'week': {str(i):{'windows':[],'max_minutes':0,'blocked':[],'rest':False}
                              for i in range(7)}},
        'materials': {'schema_version':1,'revision':1,'root':None,
                      'subjects':{s:{'path':None,'files':{}} for s in ('math2','cs408','english1','politics101')}},
        'review_policy': {'schema_version':1,'revision':1,'intervals':[1,3,7,14,30,60,90],
                          'failure_days':1,'mistake_delay_default':7},
        'mastery_policy': {'schema_version':1,'revision':1,'reference_questions':2,'reference_forms':2,
          'dimensions':{'math2':['concept','derivation','calculation','application','independent'],
                        'cs408':['concept','principle','structure','analysis','application'],
                        'english1':['vocabulary','meaning','grammar','reading','usage'],
                        'politics101':['framework','distinction','memory','analysis','argument']}}
    }


class Store:
    def __init__(self, root, at=None):
        self.root = Path(root).resolve()
        self.path = self.root/'database/studyos.sqlite3'
        self.at = now(at)
        self.c = None
        self.request = None

    def connect(self, readonly=False):
        require(self.path.is_file(), '数据库未初始化，请先在新项目根目录执行 init', 'NOT_INITIALIZED')
        c = sqlite3.connect(self.path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=5, factory=Connection)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        if not readonly:
            c.execute('PRAGMA synchronous=FULL')
        version = c.execute('PRAGMA user_version').fetchone()[0]
        if version != SCHEMA_VERSION:
            c.close()
            raise Error('SCHEMA_VERSION', '数据库版本不兼容，不自动升级', {'found':version,'supported':SCHEMA_VERSION})
        return c

    def initialize(self):
        require(not self.path.exists(), '不覆盖现有数据库', 'TARGET_EXISTS')
        require(not (self.root/'study_data').exists() and not (self.root/'study.py').exists(),
                '拒绝在旧StudyOS工作区初始化新系统','LEGACY_PROTECTED')
        # Explicit init only. Existing foreign configurations are never overwritten.
        with operation_lock(self.root):
            require(not self.path.exists(), '不覆盖现有数据库', 'TARGET_EXISTS')
            for name in defaults():
                if (self.root/f'config/{name}.json').exists():
                    config=load(self.root/f'config/{name}.json')
                    if name=='schedule':
                        from .schedule import validate
                        validate(config)
                        require(config['status']=='draft' and config['revision']==1,'首次初始化必须保留待确认草案，不自动认可已有正式配置','SCHEDULE_UNCONFIRMED')
            schema = Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
            temp = self.path.with_name('init-'+uid()+'.sqlite3')
            c = sqlite3.connect(temp)
            try:
                c.executescript(schema)
                c.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                c.execute('INSERT INTO schema_migrations VALUES(?,?,?)',(1,digest(schema.encode()),self.at))
                self.c,self.request=c,'initialization'
                c.execute('INSERT INTO commands VALUES(?,?,?,?)',('initialization',digest(b'initialization'),canonical({'initialized':True}),self.at))
                for name,data in defaults().items():
                    p=self.root/f'config/{name}.json'
                    if not p.exists(): atomic(p, canonical(data))
                    c.execute('INSERT INTO meta VALUES(?,?)',('config:'+name,sha(p)))
                    self.event('config',name,'created',{'after':load(p),'source':'initialization; schedule remains draft'})
                c.execute('INSERT INTO meta VALUES(?,?)',('database_id',uid()))
                c.commit()
            finally:
                self.c,self.request=None,None
                c.close()
            temp.replace(self.path)
            with self.connect() as c:
                c.execute('PRAGMA journal_mode=WAL')
            for folder in ['records/lessons','records/mistakes','records/assessments','records/reviews',
                           'records/handoffs','reports/daily','reports/weekly','reports/monthly','reports/engineering','backups']:
                (self.root/folder).mkdir(parents=True,exist_ok=True)
            from .handoff import render
            with self.connect(True) as c:
                render(self,c,'both')
        return {'initialized':True,'database':str(self.path),'schedule':self.config('schedule'),'new_learning_records':0}

    def config(self, name, c=None):
        p = self.root/f'config/{name}.json'
        obj=load(p)
        if c is not None:
            row=c.execute('SELECT value FROM meta WHERE key=?',('config:'+name,)).fetchone()
            require(row and row[0]==sha(p),'配置含未登记变更，需显式核验','UNREGISTERED_CONFIG',name)
        return obj

    def guard(self):
        require(not (self.root/'database/config-journal.json').exists(), '存在未完成配置提交，先执行 recover', 'RECOVERY_REQUIRED')

    def run(self, operation, raw, handler):
        require(isinstance(raw,dict),'输入必须为对象')
        request=text(raw.get('request_id'),'request_id')
        require(self.path.is_file(),'数据库未初始化','NOT_INITIALIZED')
        fingerprint=digest(canonical({'operation':operation,'input':raw}).encode())
        with operation_lock(self.root):
            self.guard()
            c=self.connect()
            try:
                c.execute('BEGIN IMMEDIATE')
                self.c,self.request=c,request
                self.request_hash=fingerprint
                previous=c.execute('SELECT * FROM commands WHERE request_id=?',(request,)).fetchone()
                if previous:
                    require(previous['request_hash']==fingerprint,'幂等键内容冲突','IDEMPOTENCY_CONFLICT')
                    result=json.loads(previous['result'])
                    c.rollback()
                    duplicate=True
                else:
                    result=handler(raw)
                    canonical(result)
                    c.execute('INSERT INTO commands VALUES(?,?,?,?)',(request,fingerprint,canonical(result),self.at))
                    c.commit()
                    duplicate=False
                journal=self.root/'database/config-journal.json'
                if journal.exists() and load(journal)['request']==request:
                    journal.unlink()
                warnings=[]
                try:
                    from .handoff import render
                    # Conservative eager refresh covers every required business trigger.
                    render(self,c,'both')
                except (OSError,Error,sqlite3.Error) as e:
                    warnings.append({'code':'HANDOFF_FAILED','message':str(e),'retry':'studyos handoff ensure --target both'})
                return {'committed':True,'duplicate':duplicate,'result':result,'warnings':warnings}
            except BaseException:
                c.rollback()
                raise
            finally:
                self.c,self.request=None,None
                c.close()

    def event(self, kind, entity, action, payload, expected=None, actor='codex', source='cli', occurred_at=None):
        last=self.c.execute('SELECT new_revision FROM audit_events WHERE entity_type=? AND entity_id=? ORDER BY event_seq DESC LIMIT 1',(kind,entity)).fetchone()
        old=last[0] if last else None
        if expected is not None:
            require(type(expected) is int and expected==(old or 1),'对象版本已变化','STALE_REVISION',{'current':old or 1})
        new=(old or 0)+1
        self.c.execute('INSERT INTO audit_events(event_id,entity_type,entity_id,event_type,occurred_at,recorded_at,actor,source,request_id,previous_revision,new_revision,payload_version,event_payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (uid(),kind,entity,action,occurred_at or self.at,now(),actor,source,self.request,old,new,1,canonical(payload)))
        return new

    def artifact(self, kind, entity, content, suffix='.json', data=None, version=1, status='ACCEPTED'):
        aid=uid(); relative=f'records/{kind}/{aid}{suffix}'
        p=safe_path(self.root,relative)
        b=content if isinstance(content,bytes) else content.encode('utf-8')
        atomic(p,b)
        self.c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?)',
                       (aid,kind,entity,version,relative,digest(b),self.at,status,canonical(data or {})))
        return aid

    def configure(self, raw, name, new):
        """Durable journal precedes replacing the authoritative file."""
        current=self.config(name,self.c)
        require(raw.get('expected_revision')==current['revision'],'配置版本冲突','STALE_REVISION')
        new={**new,'schema_version':1,'revision':current['revision']+1}
        relative=f'config/{name}.json'
        self.artifact('config',name,canonical(current))
        aid=self.artifact('config',name,canonical(new))
        journal={'name':name,'old_sha':sha(self.root/relative),'old':current,'new':new,'request':self.request,
                 'request_hash':self.request_hash,'authorization_ref':raw.get('authorization_ref'),
                 'at':self.at,'artifact_id':aid}
        # Database rows above roll back on crash; journal stores all recovery information.
        atomic(self.root/'database/config-journal.json',canonical(journal))
        atomic(self.root/relative,canonical(new))
        self.c.execute('UPDATE meta SET value=? WHERE key=?',(sha(self.root/relative),'config:'+name))
        self.event('config',name,'config_updated',{'before':current,'after':new,'authorization_ref':raw.get('authorization_ref')})
        # Removed after commit by caller, not before it.
        return {'config':new,'before':current,'after':new}


def rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql,args)]


def one(c, table, identifier):
    # Table names only supplied by internal code.
    r=c.execute(f'SELECT * FROM {table} WHERE id=?',(identifier,)).fetchone()
    require(r is not None,f'未找到 {table}: {identifier}','NOT_FOUND')
    return dict(r)


def recover(store):
    require(store.path.is_file(),'数据库未初始化','NOT_INITIALIZED')
    with operation_lock(store.root):
        journal=store.root/'database/config-journal.json'
        if not journal.exists(): return {'recovered':False}
        record=load(journal);name=record['name']
        require(name in defaults(),'恢复配置名称无效','INVALID_JOURNAL')
        path=store.root/f'config/{name}.json';new_sha=digest(canonical(record['new']).encode())
        actual=sha(path) if path.exists() else None
        require(actual in (record['old_sha'],new_sha),'配置存在第三方变化，需人工核验','RECOVERY_CONFLICT')
        with store.connect() as c:
            c.execute('BEGIN IMMEDIATE');store.c=c;store.request=record['request']
            prior=c.execute('SELECT request_hash FROM commands WHERE request_id=?',(record['request'],)).fetchone()
            if prior: require(prior[0]==record['request_hash'],'恢复请求冲突','RECOVERY_CONFLICT')
            atomic(path,canonical(record['new']))
            if not prior:
                store.artifact('config',name,canonical(record['old']))
                store.artifact('config',name,canonical(record['new']))
                c.execute('UPDATE meta SET value=? WHERE key=?',(new_sha,'config:'+name))
                store.event('config',name,'config_recovered',{'before':record['old'],'after':record['new'],'authorization_ref':record['authorization_ref']})
                result={'config':record['new'],'before':record['old'],'after':record['new']}
                c.execute('INSERT INTO commands VALUES(?,?,?,?)',(record['request'],record['request_hash'],canonical(result),record['at']))
            c.commit();journal.unlink()
        store.c=store.request=None
    from .handoff import handoff
    result=handoff(store)
    return {'recovered':True,'handoff':result}
