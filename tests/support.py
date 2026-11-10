from pathlib import Path
import json
import tempfile
import unittest
from studyos.app import execute
from studyos.common import now, uid
from studyos.storage import Store, defaults


class Case(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='studyos-test-')
        self.addCleanup(self.temp.cleanup)
        # Match Store's canonical paths, including macOS /var -> /private/var aliases.
        self.root=(Path(self.temp.name)/'work').resolve()
        self.store=Store(self.root,'2026-01-01T09:00:00+08:00')
        cfg=defaults()['materials']
        cfg['handoff_scopes']=[{'id':'linear','title':'合成线代','subject':'math2','catalog_id':'linear','directory':'subjects/linear'}]
        (self.root/'config').mkdir(parents=True)
        (self.root/'config/materials.json').write_text(json.dumps(cfg,ensure_ascii=False),encoding='utf-8')
        # Explicit synthetic schedule; production defaults contain no personal timetable.
        schedule=defaults()['schedule']
        schedule['source']='Synthetic test fixture'
        schedule['week']={str(i):{'windows':[['08:00','12:00'],['18:00','20:00']] if i<6 else [],
            'blocked':[['12:00','18:00']],'max_minutes':360 if i<6 else 0,'rest':i==6} for i in range(7)}
        (self.root/'config/schedule.json').write_text(json.dumps(schedule),encoding='utf-8')
        execute(self.store,'init')
        imported=self.do('knowledge.import',{'catalog':{'id':'linear','subject':'math2','name':'合成教材（仅测试）'},
          'points':[{'id':'k1','external_id':'LA-TEST-01','name':'独立提取','criteria':'能解释并应用','location':'PDF物理页5'},
                    {'id':'k2','external_id':'LA-TEST-02','name':'组合应用','criteria':'能辨析条件','weak':['LA-TEST-01']}]})

    def do(self,op,raw=None,at=None,store=None):
        target=store or self.store
        if at: target=Store(target.root,at)
        value=execute(target,op,{'request_id':uid(),**(raw or {})})
        return value['result'] if isinstance(value,dict) and 'committed' in value else value

    def count(self,table):
        with self.store.connect(True) as c: return c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def materials(self):
        folder=self.root/'synthetic_materials';folder.mkdir(exist_ok=True)
        (folder/'outline.md').write_text('# 大纲\nLA-TEST-01\n目标：解释并应用\n',encoding='utf-8')
        cfg=self.store.config('materials');cfg['subjects']['math2']={'path':str(folder),'files':{'outline':{'path':'outline.md'}}}
        self.do('materials.update',{'config':cfg,'expected_revision':1,'reason':'合成测试路径'})
        self.do('materials.register',{'id':'outline','subject':'math2','path_key':'outline','role':'outline',
                 'bindings':[{'knowledge_id':'k1','locator':{'line_start':1,'line_end':3}}]})

    def lesson(self):
        self.materials()
        self.do('lesson.create',{'id':'t1','knowledge_id':'k1','goal':'解释并应用','strategy':'先解释再练习',
                               'acceptance':'独立完成','unfinished_training':['新的变式'],'next_task':'进行强化训练'})
        return self.do('lesson.start',{'task_id':'t1'})

    def confirm(self,all_days=False):
        self.do('schedule.confirm',{'expected_revision':1,'authorization_ref':'synthetic user confirmation'})
        if all_days:
            cfg=self.store.config('schedule')
            cfg['week']={str(i):{'windows':[['08:00','12:00']],'blocked':[],'max_minutes':240,'rest':False} for i in range(7)}
            self.do('schedule.update',{'config':cfg,'expected_revision':2,'authorization_ref':'synthetic seven-day fixture'})

    def assessment(self,result='PASS',at='2026-01-02',assistance='NONE',kid='k1',form='recall',dimensions=None):
        dimensions=dimensions or ['concept']
        test=self.do('assessment.create',{'knowledge_id':kid,'blueprint':{'criteria':'解释目标','rationale':'该点只需一次提取',
                  'min_attempts':1,'forms':[form],'dimensions':dimensions}},at=at)
        attempt=self.do('attempt.add',{'knowledge_id':kid,'assessment_id':test['id'],'question':'合成新题 '+uid(),
             'answer':'合成实际原答','process':'解释条件','form':form,'dimensions':dimensions,'source':'codex_independent',
             'confirmed':True,'occurred_at':now(at),'assistance':assistance,'evaluation':{'result':result,'basis':'逐题标准','reference':'合成评分依据',
                 'error_type':'CONCEPT','error_reason':'合成错误，不是真实成绩'}},at=at)
        submitted=self.do('assessment.submit',{'id':test['id'],'expected_revision':1},at=at)
        return submitted,attempt
