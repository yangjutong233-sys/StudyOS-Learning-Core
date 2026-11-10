import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import traceback
from .common import Error, canonical, load
from .storage import Store
from .app import execute


def main(argv=None):
    # Stable UTF-8 transport even when Windows redirected pipes default to GBK.
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser(description='StudyOS：证据档案、复习与AI协作接口')
    p.add_argument('group');p.add_argument('action',nargs='?');p.add_argument('subaction',nargs='?')
    p.add_argument('--root');p.add_argument('--input',help='JSON文件；-为stdin');p.add_argument('--at',help='注入业务时钟，不修改系统时间')
    p.add_argument('--json',action='store_true');p.add_argument('--dry-run',action='store_true',default=None)
    for name in ('id','task','subject','knowledge-id','reason','target','path','destination','source','mode','day','authorization-ref','entity-type','request-id','name','material-id'):
        p.add_argument('--'+name)
    p.add_argument('--level',type=int);p.add_argument('--limit',type=int);p.add_argument('--cursor',type=int)
    p.add_argument('--material-version',type=int);p.add_argument('--task-version',type=int)
    a=p.parse_args(argv)
    try:
        root=a.root or os.environ.get('STUDYOS_ROOT')
        if root is None:
            source_root=Path(__file__).resolve().parents[2]
            if (source_root/'pyproject.toml').is_file(): root=str(source_root)
            else: raise Error('ROOT_REQUIRED','请显式提供 --root 或 STUDYOS_ROOT')
        raw={}
        if a.input:
            raw=json.loads(sys.stdin.read()) if a.input=='-' else load(a.input)
            if not isinstance(raw,dict): raise Error('VALIDATION_ERROR','输入必须为JSON对象')
        for key,value in vars(a).items():
            if key not in ('group','action','subaction','root','input','at','json') and value is not None:
                raw['task_id' if key=='task' else key]=value
        operation='.'.join(x for x in (a.group,a.action,a.subaction) if x)
        result=execute(Store(root,a.at),operation,raw)
        envelope={'schema_version':1,'ok':True,'data':result,'warnings':result.get('warnings',[]) if isinstance(result,dict) else [],'error':None}
        print(canonical(envelope) if a.json else json.dumps(envelope,ensure_ascii=False,indent=2,allow_nan=False))
        return 0
    except (Error,ValueError,KeyError,OSError,sqlite3.Error) as e:
        code=e.code if isinstance(e,Error) else 'DATABASE_BUSY' if isinstance(e,sqlite3.OperationalError) and 'locked' in str(e) else 'STORAGE_ERROR' if isinstance(e,(OSError,sqlite3.Error)) else 'VALIDATION_ERROR'
        envelope={'schema_version':1,'ok':False,'data':None,'warnings':[],'error':{'code':code,'message':str(e),'details':getattr(e,'details',None)}}
        print(canonical(envelope))
        return 3 if code in ('STALE_REVISION','IDEMPOTENCY_CONFLICT','ID_CONFLICT','REPORT_CONFLICT') else 4 if code in ('STORAGE_ERROR','DATABASE_BUSY') or code.startswith(('BACKUP_','RESTORE_')) else 2
    except Exception as e:
        # Keep the transport stable for unexpected bugs, without misclassifying them
        # as bad input or claiming a possibly committed operation was rolled back.
        diagnostic=None
        try:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='studyos-error-',suffix='.log',delete=False) as f:
                diagnostic=f.name
                f.write('Unexpected exception: '+type(e).__name__+'\n')
                f.write(''.join(traceback.format_list(traceback.extract_tb(e.__traceback__))))
        except OSError:diagnostic=None
        print(canonical({'schema_version':1,'ok':False,'data':None,'warnings':[],
            'error':{'code':'INTERNAL_ERROR','message':'内部错误；提交状态尚未确认，请查询审计或使用相同request_id重试。',
                     'details':{'diagnostic_path':diagnostic,'exception_type':type(e).__name__}}}))
        return 4
