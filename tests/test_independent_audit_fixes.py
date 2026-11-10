"""Regressions for the independent audit; synthetic records in temporary roots only."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from studyos.app import execute
from studyos.common import Error, now, uid
from studyos.storage import Store
from tests.support import Case


class AuditFixTests(Case):
    def create_test(self, at='2026-01-02T09:00:00+08:00', **blueprint):
        return self.do('assessment.create', {'knowledge_id':'k1', 'blueprint':{
            'criteria':'独立解释', 'rationale':'合成考核', 'min_attempts':1,
            'forms':['recall'], 'dimensions':['concept'], **blueprint}}, at=at)

    def answer(self, assessment, at, result='PASS', actual=True):
        data={'knowledge_id':'k1','assessment_id':assessment['id'],'question':'新题 '+uid(),
              'answer':'原答','process':'原过程','form':'recall','dimensions':['concept'],
              'source':'codex_independent','confirmed':True,'assistance':'NONE',
              'evaluation':{'result':result,'basis':'评分依据','reference':'参考标准'}}
        if actual is not None:
            data['occurred_at']=now(at) if actual is True else actual
        return self.do('attempt.add',data,at=at)

    def evidence(self, at, result='PASS', actual=True):
        # A legitimate late import refers to a blueprint saved before the answer.
        created=actual if isinstance(actual,str) and now(actual)<now(at) else at
        a=self.create_test(created);self.answer(a,at,result,actual)
        return self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at=at)

    def series(self, at=None):
        return self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'独立审计回归'},at=at)

    def test_AUD01_unknown_remains_due_and_accepts_fresh_supplement(self):
        s=self.series();a=self.evidence('2026-01-02','UNDETERMINED')
        raw={'request_id':'unknown-result','id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']}
        first=execute(Store(self.root,'2026-01-02'),'review.complete',raw)
        self.assertIsNone(first['result']['next'])
        due=execute(Store(self.root,'2026-01-02'),'review.due')
        self.assertIn(s['task']['id'],[t['id'] for t in due])
        history=execute(self.store,'review.history',{'id':s['id']})
        self.assertEqual(history[0]['status'],'ACTIVE');self.assertIsNone(history[0]['completed_at'])
        events=self.count('audit_events')
        self.assertTrue(execute(Store(self.root,'2026-01-02'),'review.complete',raw)['duplicate'])
        self.assertEqual(events,self.count('audit_events'))
        with self.assertRaises(Error) as reused:
            self.do('review.complete',{**raw,'request_id':uid(),'expected_revision':2},at='2026-01-02')
        self.assertEqual(reused.exception.code,'EVIDENCE_REUSED')
        b=self.evidence('2026-01-03')
        final=self.do('review.complete',{'id':s['task']['id'],'expected_revision':2,'assessment_id':b['id']},at='2026-01-03')
        self.assertEqual(final['stage'],1);self.assertEqual(final['next']['suggested_date'],'2026-01-06')
        with self.store.connect(True) as c:
            actions=[r[0] for r in c.execute("SELECT event_type FROM audit_events WHERE entity_type='review_task' AND entity_id=? ORDER BY event_seq",(s['task']['id'],))]
            self.assertIn('evidence_pending',actions);self.assertEqual(actions[-1],'completed')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM artifacts WHERE kind='reviews' AND entity_id=?",(s['task']['id'],)).fetchone()[0],2)

    def test_AUD02_delay_cannot_grant_same_day_mastery(self):
        a=self.create_test(delay_days=7);self.answer(a,'2026-01-02T09:00:00+08:00')
        pending=self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-02T09:00:00+08:00')
        self.assertEqual(pending['result'],'DELAY_PENDING')
        self.assertEqual(execute(self.store,'assessment.show',{'id':a['id']})['status'],'OPEN')
        self.assertEqual(self.count('mastery_assessments'),0)
        self.answer(a,'2026-01-09T08:59:59+08:00')
        early=self.do('assessment.submit',{'id':a['id'],'expected_revision':2},at='2026-01-09T08:59:59+08:00')
        self.assertEqual(early['result'],'DELAY_PENDING')
        self.answer(a,'2026-01-09T01:00:00+00:00')
        passed=self.do('assessment.submit',{'id':a['id'],'expected_revision':3},at='2026-01-09T09:00:00+08:00')
        self.assertEqual(passed['result'],'PASS');self.assertEqual(self.count('attempts'),3)
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'MASTERED')

    def test_AUD03_old_answer_cannot_advance_current_review(self):
        s=self.series(at='2026-01-09');a=self.evidence('2026-01-10',actual='2026-01-01T09:00:00+08:00')
        with self.assertRaises(Error) as rejected:
            self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-10')
        self.assertEqual(rejected.exception.code,'EVIDENCE_TIME_INVALID')
        self.assertEqual(execute(self.store,'review.history',{'id':s['id']})[0]['status'],'PENDING')

    def test_AUD04_registered_new_material_is_readable(self):
        self.materials()
        cfg=self.store.config('materials');folder=Path(cfg['subjects']['math2']['path'])
        (folder/'v2.md').write_text('# 新大纲\nLA-TEST-01\n新掌握标准\n',encoding='utf-8')
        cfg['subjects']['math2']['files']['outline']['path']='v2.md'
        self.do('materials.update',{'config':cfg,'expected_revision':2,'reason':'显式升级教材'})
        self.do('materials.register',{'id':'outline','expected_revision':1,'subject':'math2','path_key':'outline','role':'outline',
            'bindings':[{'knowledge_id':'k1','locator':{'line_start':1,'line_end':3}}]})
        found=execute(self.store,'materials.read',{'knowledge_id':'k1','level':2,'reason':'阅读当前大纲'})
        self.assertEqual(len(found['materials']),1);self.assertEqual(found['materials'][0]['version'],2)
        self.assertIn('新掌握标准',found['materials'][0]['text'])

    def test_AUD05_wrong_nested_type_returns_json_validation_error(self):
        before=self.count('audit_events')
        result=subprocess.run([sys.executable,'-m','studyos','attempt','add','--root',str(self.root),'--json','--input','-'],
            input=json.dumps({'request_id':uid(),'knowledge_id':'k1','question':'题','answer':'答','process':'过程',
                 'form':'recall','source':'codex_independent','confirmed':True,'assistance':'NONE','evaluation':[]}),
            encoding='utf-8',text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,2,(result.stdout,result.stderr))
        self.assertEqual(json.loads(result.stdout)['error']['code'],'VALIDATION_ERROR')
        self.assertNotIn('Traceback',result.stderr);self.assertEqual(before,self.count('audit_events'))

    def test_unknown_interval_override_cannot_settle_or_advance(self):
        s=self.series();a=self.evidence('2026-01-02','UNDETERMINED')
        before=self.count('audit_events')
        for extra in ({'interval_days':3},{'reset':True}):
            with self.subTest(extra=extra),self.assertRaises(Error):
                self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,
                    'assessment_id':a['id'],'reason':'不能替代新证据',**extra},at='2026-01-02')
        self.assertEqual(before,self.count('audit_events'))
        self.assertEqual(execute(self.store,'review.history',{'id':s['id']})[0]['status'],'PENDING')

    def test_multiple_unknown_evaluations_preserve_all_used_ids(self):
        s=self.series()
        evidence=[]
        for revision in (1,2):
            a=self.evidence('2026-01-02','UNDETERMINED');evidence.append(a['id'])
            self.do('review.complete',{'id':s['task']['id'],'expected_revision':revision,'assessment_id':a['id']},at='2026-01-02')
        saved=execute(self.store,'review.history',{'id':s['id']})[0]
        self.assertEqual(json.loads(saved['data'])['used_assessment_ids'],evidence)
        with self.assertRaises(Error) as reused:
            self.do('review.complete',{'id':s['task']['id'],'expected_revision':3,'assessment_id':evidence[0]},at='2026-01-02')
        self.assertEqual(reused.exception.code,'EVIDENCE_REUSED')

    def test_delay_value_validation_and_recording_time_not_evidence(self):
        before=self.count('assessments')
        for value in (-1,True,'7',1.5,None,{},[],3651):
            with self.subTest(value=value),self.assertRaises(Error):self.create_test(delay_days=value)
        self.assertEqual(self.count('assessments'),before)
        a=self.create_test(delay_days=1)
        self.answer(a,'2026-01-04',actual=None)
        result=self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-04')
        self.assertEqual(result['result'],'DELAY_PENDING');self.assertEqual(self.count('mastery_assessments'),0)

    def test_delay_requires_complete_post_delay_blueprint_coverage_and_replay(self):
        a=self.create_test(delay_days=1,min_attempts=2,forms=['recall','application'],dimensions=['concept','application'])
        self.answer(a,'2026-01-02T09:00:00+08:00')
        data={'knowledge_id':'k1','assessment_id':a['id'],'question':'应用新题 '+uid(),'answer':'原答','process':'推导',
              'form':'application','dimensions':['application'],'source':'codex_independent','assistance':'NONE',
              'confirmed':True,'occurred_at':'2026-01-02T09:00:00+08:00',
              'evaluation':{'result':'PASS','basis':'依据','reference':'标准'}}
        self.do('attempt.add',data,at='2026-01-02T09:00:00+08:00')
        self.answer(a,'2026-01-03T09:00:00+08:00')
        raw={'request_id':'delay-replay','id':a['id'],'expected_revision':1}
        pending=execute(Store(self.root,'2026-01-03T09:00:00+08:00'),'assessment.submit',raw)
        self.assertEqual(pending['result']['result'],'DELAY_PENDING')
        count=self.count('audit_events')
        self.assertTrue(execute(Store(self.root,'2026-01-03T09:00:00+08:00'),'assessment.submit',raw)['duplicate'])
        self.assertEqual(count,self.count('audit_events'))
        self.do('attempt.add',{**data,'question':'延迟应用新题 '+uid(),'occurred_at':'2026-01-03T09:00:00+08:00'},at='2026-01-03T09:00:00+08:00')
        passed=self.do('assessment.submit',{'id':a['id'],'expected_revision':2},at='2026-01-03T09:00:00+08:00')
        self.assertEqual(passed['result'],'PASS')
        with self.store.connect(True) as c:
            payload=json.loads(c.execute('SELECT data FROM mastery_assessments').fetchone()[0])
            self.assertEqual(payload['retention'],'VERIFIED');self.assertEqual(len(payload['attempt_ids']),2)
            event=json.loads(c.execute("SELECT event_payload FROM audit_events WHERE entity_type='assessment' AND entity_id=? AND event_type='completed'",(a['id'],)).fetchone()[0])
            self.assertEqual(len(event['attempt_ids']),2)
        self.assertEqual(self.count('attempts'),4)

    def test_delay_anchor_is_validated_saved_evidence_not_arbitrary_timestamp(self):
        previous=self.evidence('2026-01-01T09:00:00+08:00')
        a=self.create_test(at='2026-01-08T09:00:00+08:00',delay_days=7,
            delay_anchor={'entity_type':'assessment','entity_id':previous['id']})
        self.assertEqual(a['blueprint']['delay_anchor_at'],'2026-01-01T09:00:00+08:00')
        self.answer(a,'2026-01-08T09:00:00+08:00')
        self.assertEqual(self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-08T09:00:00+08:00')['result'],'PASS')
        other,_=self.assessment(kid='k2')
        for source in ({'entity_type':'timestamp','entity_id':'2025-01-01'},
                       {'entity_type':'assessment','entity_id':other['id']}):
            with self.subTest(source=source),self.assertRaises(Error):self.create_test(delay_days=7,delay_anchor=source)

    def test_completed_lesson_record_can_anchor_delay(self):
        session=self.lesson()
        completed=self.do('lesson.finish',{'session_id':session['session_id'],'expected_revision':1,
            'checkpoint':'教学完成，待独立验证','covered':['概念']})
        a=self.create_test(at='2026-01-02T09:00:00+08:00',delay_days=1,
            delay_anchor={'entity_type':'session_record','entity_id':completed['record_id']})
        self.answer(a,'2026-01-02T09:00:00+08:00')
        self.assertEqual(self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-02T09:00:00+08:00')['result'],'PASS')

    def test_review_requires_confirmed_time_and_original_delay_window(self):
        s=self.series()
        cases=[(None,'2026-01-02','EVIDENCE_TIME_UNVERIFIED'),
               ('2026-01-01T12:00:00+08:00','2026-01-02','EVIDENCE_TIME_INVALID')]
        for actual,recorded,code in cases:
            with self.subTest(actual=actual):
                a=self.evidence(recorded,result='UNDETERMINED' if actual is None else 'PASS',actual=actual)
                with self.assertRaises(Error) as rejected:
                    self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']},at=recorded)
                self.assertEqual(rejected.exception.code,code)
        self.assertEqual(execute(self.store,'review.history',{'id':s['id']})[0]['status'],'PENDING')

    def test_late_recording_uses_actual_completion_date_not_recording_date(self):
        s=self.series()
        a=self.evidence('2026-01-05T09:00:00+08:00',actual='2026-01-02T15:59:59+00:00')
        done=self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-05T09:00:00+08:00')
        self.assertEqual(done['completed_at'],'2026-01-02T23:59:59+08:00')
        self.assertEqual(done['next']['suggested_date'],'2026-01-05')
        with self.store.connect(True) as c:
            event=c.execute("SELECT occurred_at,event_payload FROM audit_events WHERE entity_type='review_task' AND entity_id=? AND event_type='completed'",(s['task']['id'],)).fetchone()
            self.assertEqual(event[0],done['completed_at']);self.assertEqual(json.loads(event[1])['evaluated_at'],'2026-01-05T09:00:00+08:00')

    def upgrade_material(self,same_path=False):
        cfg=self.store.config('materials');folder=Path(cfg['subjects']['math2']['path'])
        path=folder/('outline.md' if same_path else 'v2.md')
        path.write_text('# 二版大纲\nLA-TEST-01\n标准二\n',encoding='utf-8')
        if not same_path:
            cfg['subjects']['math2']['files']['outline']['path']='v2.md'
            self.do('materials.update',{'config':cfg,'expected_revision':2,'reason':'显式变更教材路径'})
        self.do('materials.register',{'id':'outline','expected_revision':1,'subject':'math2','path_key':'outline','role':'outline',
            'bindings':[{'knowledge_id':'k1','locator':{'line_start':1,'line_end':3}}]})

    def test_current_and_pinned_historical_material_paths_stay_separate(self):
        self.materials()
        task={'id':'pinned','knowledge_id':'k1','goal':'g','strategy':'s','acceptance':'a','next_task':'n'}
        self.do('lesson.create',task);self.upgrade_material()
        args={'knowledge_id':'k1','level':2,'reason':'按任务检查版本'}
        old=execute(self.store,'materials.read',{**args,'task_id':'pinned'})['materials']
        current=execute(self.store,'materials.read',args)['materials']
        explicit=execute(self.store,'materials.read',{**args,'material_id':'outline','material_version':1})['materials']
        self.assertEqual(old,explicit);self.assertEqual(old[0]['version'],1);self.assertEqual(current[0]['version'],2)
        self.assertIn('目标：解释并应用',old[0]['text']);self.assertIn('标准二',current[0]['text'])
        self.do('lesson.prepare',{'task_id':'pinned'})
        packet=(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn('outline.md',packet);self.assertNotIn('v2.md',packet)
        self.assertNotIn('版本已变化',packet)
        self.do('lesson.create',{**task,'expected_revision':1})
        fresh=execute(self.store,'materials.read',{**args,'task_id':'pinned'})['materials']
        self.assertEqual(fresh[0]['version'],2)
        historical=execute(self.store,'materials.read',{**args,'task_id':'pinned','task_version':1})['materials']
        self.assertEqual(historical,old)
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'UNASSESSED')

    def test_same_path_new_version_reads_current_and_old_missing_is_explicit(self):
        self.materials();self.upgrade_material(same_path=True)
        args={'knowledge_id':'k1','level':2,'reason':'核对已登记材料'}
        found=execute(self.store,'materials.read',args)['materials']
        self.assertEqual(found[0]['version'],2)
        with self.assertRaises(Error) as changed:
            execute(self.store,'materials.read',{**args,'material_id':'outline','material_version':1})
        self.assertEqual(changed.exception.code,'MATERIAL_CHANGED')
        with self.assertRaises(Error):execute(self.store,'materials.read',{**args,'material_version':1})
        with self.assertRaises(Error):execute(self.store,'materials.read',{**args,'material_id':'absent','material_version':1})

    def test_nested_invalid_types_are_business_errors_without_partial_writes(self):
        samples=[('attempt add',{'evaluation':[]}),('attempt add',{'evaluation':None}),
            ('attempt evaluate',{'evaluation':'bad'}),('knowledge import',{'catalog':[]}),
            ('knowledge import',{'points':[[]]}),('knowledge import',{'points':[{'strong':[{}]}]}),
            ('assessment create',{'blueprint':[]}),('assessment create',{'blueprint':{'dimensions':[{}]}}),
            ('assessment create',{'blueprint':{'forms':[[]]}}),('materials register',{'bindings':[1]}),
            ('materials register',{'bindings':[{'locator':[]}]}),('plan apply',{'items':[[]]}),
            ('materials update',{'config':{'subjects':[]}}),('materials update',{'config':{'subjects':{'math2':[]}}}),
            ('schedule update',{'config':{'week':{'0':[]}}}),('policy update',{'config':{'dimensions':[]}}),
            ('review create',{'members':[{}]}),('review complete',{'assessment_ids':{}}),
            ('lesson create',{'context_attempt_ids':[{}]}),('attempt add',{'answer_seen':'false'}),
            ('summary',{'id':[]} ),('summary',[])]
        before=self.count('audit_events');commands=self.count('commands')
        for op,raw in samples:
            with self.subTest(op=op,raw=raw):
                payload={'request_id':uid(),**raw} if isinstance(raw,dict) else raw
                result=subprocess.run([sys.executable,'-m','studyos',*op.split(),'--root',str(self.root),'--json','--input','-'],
                    input=json.dumps(payload),encoding='utf-8',text=True,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,2,(result.stdout,result.stderr))
                self.assertEqual(json.loads(result.stdout)['error']['code'],'VALIDATION_ERROR')
                self.assertNotIn('Traceback',result.stderr)
        self.assertEqual(self.count('audit_events'),before);self.assertEqual(self.count('commands'),commands)

    def test_malformed_report_nested_shapes_are_validated_before_fields(self):
        from studyos.reports import validate_pair
        base={'report_id':'r','version':1,'task_id':'t','session_id':'s','checkpoint':'c','attempts':[]}
        for override in ({'attempts':[[]]},{'attempts':[{'id':'a','evaluation':[]}]},{'covered':{}},{'attempts':None}):
            with self.subTest(override=override),self.assertRaises(Error):validate_pair('',{**base,**override})

    def test_internal_bug_has_json_boundary_and_safe_diagnostic(self):
        from studyos.cli import main
        out=io.StringIO()
        with patch('studyos.cli.execute',side_effect=RuntimeError('DO_NOT_LOG_USER_PAYLOAD')),contextlib.redirect_stdout(out):
            code=main(['summary','--root',str(self.root),'--json'])
        result=json.loads(out.getvalue());self.assertEqual(code,4)
        self.assertEqual(result['error']['code'],'INTERNAL_ERROR')
        path=Path(result['error']['details']['diagnostic_path']);self.addCleanup(path.unlink,missing_ok=True)
        self.assertFalse(path.is_relative_to(self.root));self.assertNotIn('DO_NOT_LOG_USER_PAYLOAD',path.read_text(encoding='utf-8'))

    def test_complete_backup_retains_distinct_registered_material_versions(self):
        self.materials();self.upgrade_material()
        backup=execute(self.store,'backup.create',{'mode':'complete'})
        manifest=json.loads((Path(backup['path'])/'manifest.json').read_text(encoding='utf-8'))
        versions=manifest['external_mapping']
        self.assertEqual({v['material_version'] for v in versions},{1,2})
        self.assertEqual(len({v['sha256'] for v in versions}),2)
        self.assertTrue(backup['restore_verified'])

    def test_legacy_open_delay_blueprint_uses_original_creation_event(self):
        a=self.create_test(delay_days=1)
        with self.store.connect() as c:
            b=json.loads(c.execute('SELECT blueprint FROM assessments WHERE id=?',(a['id'],)).fetchone()[0])
            for key in ('delay_anchor_at','delay_anchor_source','delay_eligible_at'):b.pop(key)
            c.execute('UPDATE assessments SET blueprint=? WHERE id=?',(json.dumps(b),a['id']))
        self.answer(a,'2026-01-02T09:00:00+08:00')
        pending=self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-05T09:00:00+08:00')
        self.assertEqual(pending['result'],'DELAY_PENDING')
        self.assertEqual(pending['delay_eligible_at'],'2026-01-03T09:00:00+08:00')
        report=execute(self.store,'report.show',{'id':pending['report_artifact_id']})['text']
        self.assertIn('待延迟补证据',report);self.assertNotIn('完成日期',report)
        self.answer(a,'2026-01-05T09:00:00+08:00')
        self.assertEqual(self.do('assessment.submit',{'id':a['id'],'expected_revision':2},at='2026-01-05T09:00:00+08:00')['result'],'PASS')
        with self.store.connect(True) as c:
            self.assertEqual(json.loads(c.execute('SELECT blueprint FROM assessments WHERE id=?',(a['id'],)).fetchone()[0]),b)

    def test_delay_boundary_keeps_fractional_seconds(self):
        a=self.create_test(at='2026-01-02T09:00:00.500000+08:00',delay_days=1)
        self.answer(a,'2026-01-03T09:00:00.499999+08:00')
        pending=self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-03T09:00:00.499999+08:00')
        self.assertEqual(pending['result'],'DELAY_PENDING')
        self.answer(a,'2026-01-03T09:00:00.500000+08:00')
        self.assertEqual(self.do('assessment.submit',{'id':a['id'],'expected_revision':2},at='2026-01-03T09:00:00.500000+08:00')['result'],'PASS')
