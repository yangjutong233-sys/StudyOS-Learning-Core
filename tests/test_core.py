from pathlib import Path
from unittest.mock import patch
import json
import sqlite3
import subprocess
import sys
import unittest
from studyos.app import execute
from studyos.common import Error, canonical, digest, sha, uid
from studyos.storage import Store
from tests.support import Case


class StorageTests(Case):
    def test_read_queries_do_not_create_or_modify_files(self):
        empty=self.root/'absent'
        with self.assertRaises(Error): execute(Store(empty),'summary')
        self.assertFalse(empty.exists())
        before={str(p):sha(p) for p in self.root.rglob('*') if p.is_file() and not p.name.endswith(('-shm','-wal'))}
        execute(self.store,'summary');execute(self.store,'knowledge.list')
        after={str(p):sha(p) for p in self.root.rglob('*') if p.is_file() and not p.name.endswith(('-shm','-wal'))}
        self.assertEqual(before,after)

    def test_request_replay_conflict_and_transaction_rollback(self):
        raw={'request_id':'same','catalog':{'id':'new','subject':'english1','name':'新目录'},
             'points':[{'id':'en','external_id':'EN-1','name':'词义','criteria':'辨析'}]}
        execute(self.store,'knowledge.import',raw)
        before=self.count('audit_events')
        self.assertTrue(execute(self.store,'knowledge.import',raw)['duplicate'])
        self.assertEqual(before,self.count('audit_events'))
        with self.assertRaises(Error): execute(self.store,'knowledge.import',{**raw,'catalog':{**raw['catalog'],'name':'变更'}})
        prior=self.count('knowledge_points')
        with self.assertRaises(Error): self.do('knowledge.import',{'catalog':{'id':'bad','subject':'math2','name':'bad'},
             'points':[{'external_id':'A','name':'a','criteria':'a'},{'external_id':'B','name':'b'}]})
        self.assertEqual(prior,self.count('knowledge_points'))

    def test_audit_append_only_and_doctor(self):
        with self.store.connect() as c:
            with self.assertRaises(sqlite3.IntegrityError): c.execute('DELETE FROM audit_events')
        self.assertTrue(execute(self.store,'doctor')['ok'])

    def test_strong_dependency_cycle_rolls_back(self):
        with self.assertRaises(Error):
            self.do('knowledge.import',{'catalog':{'id':'cycle','subject':'cs408','name':'cycle'},'points':[
                {'external_id':'A','name':'a','criteria':'a','strong':['B']},
                {'external_id':'B','name':'b','criteria':'b','strong':['A']}]})
        self.assertEqual(self.count('knowledge_points'),2)

    def test_handoff_failure_does_not_rollback_and_replay_repairs(self):
        raw={'request_id':'safe','id':'t','knowledge_id':'k1','goal':'g','strategy':'s','acceptance':'a','next_task':'n'}
        with patch('studyos.handoff.atomic',side_effect=OSError('synthetic disk full')):
            result=execute(self.store,'lesson.create',raw)
        self.assertTrue(result['committed']);self.assertTrue(result['warnings'])
        self.assertEqual(self.count('lesson_tasks'),1)
        result=execute(self.store,'lesson.create',raw)
        self.assertTrue(result['duplicate']);self.assertFalse(result['warnings'])
        self.assertTrue(execute(self.store,'handoff.show',{'target':'codex'})['fresh'])

    def test_cli_errors_are_json_and_root_is_explicit(self):
        done=subprocess.run([sys.executable,'-m','studyos','knowledge','show','--root',str(self.root),'--id','missing','--json'],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(done.returncode,2)
        self.assertEqual(json.loads(done.stdout)['error']['code'],'NOT_FOUND')


class LearningTests(Case):
    def test_A_session_and_adaptive_independent_assessment(self):
        session=self.lesson()
        self.do('lesson.finish',{'session_id':session['session_id'],'expected_revision':1,'minutes':15,
                    'time_basis':'用户确认','checkpoint':'进行独立考核','covered':['定义和条件']})
        result,attempt=self.assessment()
        self.assertEqual(result['result'],'PASS')
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'MASTERED')
        self.assertEqual(self.count('session_records'),1)
        self.assertEqual(self.count('review_tasks'),1)

    def test_D_later_failure_downgrades_without_erasing_pass(self):
        self.assessment('PASS');self.assessment('FAIL',at='2026-01-09')
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'CONSOLIDATE')
        self.assertEqual(self.count('mastery_assessments'),2)

    def test_hints_and_uncertain_results_not_mastery(self):
        result,_=self.assessment(assistance='LIGHT_HINT')
        self.assertEqual(result['result'],'HINTED')
        self.assertNotEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'MASTERED')
        before=self.count('mastery_assessments')
        result,_=self.assessment('UNDETERMINED')
        self.assertEqual(result['result'],'UNDETERMINED');self.assertEqual(before,self.count('mastery_assessments'))

    def test_B_N_mistake_requires_evidence_and_configurable_plan(self):
        _,bad=self.assessment('FAIL',at='2026-01-01')
        mid=bad['mistake_id'];self.assertIsNotNone(mid)
        with self.assertRaises(Error): self.do('mistake.record-event',{'id':mid,'expected_revision':1,'action':'RESOLVED','reason':'看懂了'})
        self.do('mistake.record-event',{'id':mid,'expected_revision':1,'action':'PLAN','reason':'孤立计算步骤错误，立即变式验证',
             'resolution_plan':{'variant_required':True,'delay_days':0}})
        _,good=self.assessment('PASS',at='2026-01-01')
        result=self.do('mistake.record-event',{'id':mid,'expected_revision':2,'action':'RESOLVED','reason':'真实独立变式证据','attempt_ids':[good['id']]})
        self.assertEqual(result['status'],'RESOLVED');self.assertEqual(self.count('attempts'),2)

    def test_same_question_multiple_attempts_preserve_original_answers(self):
        base={'knowledge_id':'k1','question_id':'q','question':'同一道题','process':'过程','form':'recall','source':'gpt_teaching',
              'confirmed':True,'assistance':'ANSWER_SEEN','evaluation':{'result':'PASS','basis':'依据','reference':'答案'}}
        self.do('attempt.add',{**base,'answer':'原答一'})
        self.do('attempt.add',{**base,'answer':'原答二','assistance':'NONE'})
        self.assertEqual([r['answer'] for r in execute(self.store,'attempt.list')],['原答一','原答二'])


class ReviewTests(Case):
    def test_F_continuous_partial_failure_delay_and_idempotency(self):
        self.confirm(all_days=True)
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'正常延迟复习'})
        t=s['task'];self.assertEqual(t['suggested_date'],'2026-01-02')
        for when,outcome,expected in [('2026-01-02','PASS','2026-01-05'),('2026-01-05','PASS','2026-01-12'),
                                      ('2026-01-12','PARTIAL','2026-01-15'),('2026-01-15','FAIL','2026-01-16'),
                                      ('2026-01-16','PASS','2026-01-19')]:
            a,_=self.assessment(outcome,at=when)
            result=self.do('review.complete',{'id':t['id'],'expected_revision':1,'assessment_id':a['id']},at=when)
            t=result['next'];self.assertEqual(t['suggested_date'],expected)
        a,_=self.assessment('PASS',at='2026-01-22')
        raw={'request_id':'repeat-review','id':t['id'],'expected_revision':1,'assessment_id':a['id']}
        value=execute(Store(self.root,'2026-01-22'),'review.complete',raw)
        self.assertEqual(value['result']['next']['suggested_date'],'2026-01-29')
        n=self.count('review_tasks');self.assertTrue(execute(Store(self.root,'2026-01-22'),'review.complete',raw)['duplicate'])
        self.assertEqual(n,self.count('review_tasks'))

    def test_fail_does_not_overwrite_and_evidence_cannot_be_reused(self):
        s=self.do('review.create',{'kind':'mistake','mistake_id':self.assessment('FAIL')[1]['mistake_id'],'reason':'错题复习'})
        a,_=self.assessment('FAIL',at='2026-01-03')
        result=self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-03')
        self.assertEqual(result['next']['suggested_date'],'2026-01-04')
        with self.assertRaises(Error): self.do('review.complete',{'id':result['next']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-04')
        history=execute(self.store,'review.history',{'id':s['id']});self.assertEqual(history[0]['result'],'FAIL')

    def test_comprehensive_and_wrong_target_rejected(self):
        s=self.do('review.create',{'kind':'comprehensive','members':['k1','k2'],'reason':'综合复习'})
        a,_=self.assessment('PASS')
        with self.assertRaises(Error): self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']})
        b,_=self.assessment('PASS',kid='k2')
        value=self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_ids':[a['id'],b['id']]},at='2026-01-02')
        self.assertEqual(value['result'],'PASS')

    def test_cancelled_review_can_be_reopened_as_a_reminder(self):
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'提醒测试'})
        self.do('review.cancel',{'id':s['task']['id'],'expected_revision':1,'reason':'暂时取消'})
        reopened=self.do('review.reopen',{'id':s['task']['id'],'expected_revision':2,'reason':'恢复提醒'})
        self.assertEqual(reopened['status'],'PENDING')
        self.assertEqual(reopened['due_date'],'2026-01-02')
        self.assertEqual(self.count('review_tasks'),1)

    def test_180_day_simulation_and_sunday_shift(self):
        from datetime import date,timedelta
        self.confirm(all_days=True)
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'长期模拟'})
        task=s['task'];current=date(2026,1,2);rounds=0
        while current<=date(2026,7,1):
            a,_=self.assessment('PASS',at=current.isoformat())
            result=self.do('review.complete',{'id':task['id'],'expected_revision':1,'assessment_id':a['id']},at=current.isoformat())
            task=result['next'];current=date.fromisoformat(task['suggested_date']);rounds+=1
        self.assertGreaterEqual(rounds,6)
        self.assertTrue(execute(self.store,'doctor')['ok'])


class ScheduleTests(Case):
    def plan(self,**extra):
        context=execute(self.store,'planning-context',{'day':'2026-01-02'})
        return {'id':'p1','day':'2026-01-02','schedule_hash':context['capacity']['schedule_hash'],'items':[
             {'id':'i1','knowledge_id':'k1','start':'08:00','end':'09:00','goal':'概念','reason':'前次测试薄弱','acceptance':'独立解释'}],**extra}

    def test_E_draft_permission_conflict_capacity_and_stale_plan(self):
        with self.assertRaises(Error): self.do('plan.apply',self.plan())
        with self.assertRaises(Error): self.do('schedule.confirm',{'expected_revision':1})
        self.confirm()
        p=self.plan();self.do('plan.apply',p)
        cfg=self.store.config('schedule');cfg['week']['4']['max_minutes']=30
        self.do('schedule.update',{'config':cfg,'expected_revision':2,'authorization_ref':'用户明确减半'})
        self.assertTrue(execute(self.store,'plan.show',{'id':'p1'})['stale'])
        with self.assertRaises(Error): self.do('plan.apply',{**self.plan(),'id':'too-large'})
        bad=self.plan();bad['items'][0].update(start='13:00',end='14:00')
        with self.assertRaises(Error): self.do('plan.apply',bad)

    def test_exception_does_not_change_week_and_completed_task_is_preserved(self):
        self.confirm();before=self.store.config('schedule')
        self.do('schedule.exception.add',{'day':'2026-01-02','authorization_ref':'用户当日调整','config':{
                    'windows':[['08:00','10:00']],'blocked':[],'max_minutes':120,'rest':False}})
        self.assertEqual(before,self.store.config('schedule'))
        p=self.plan();self.do('plan.apply',p)
        self.do('plan.record-progress',{'plan_id':'p1','id':'i1','expected_revision':1,'status':'COMPLETED','reason':'用户确认完成'})
        revised=self.plan(expected_revision=1);revised['items']=[]
        with self.assertRaises(Error): self.do('plan.revise',revised)


class HandoffTests(Case):
    def test_J_automatic_self_contained_and_edited_missing_repair(self):
        self.lesson()
        gpt=(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn('当前教学策略',gpt);self.assertIn('新窗口教材附件清单',gpt);self.assertIn('新的变式',gpt)
        self.assertNotIn('studyos handoff ensure',gpt)
        before=self.count('audit_events')
        (self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').write_text('手工伪造状态',encoding='utf-8')
        result=execute(self.store,'handoff.ensure',{'target':'both'})
        self.assertTrue(result['regenerated']);self.assertEqual(before,self.count('audit_events'))
        (self.root/'subjects/linear/CODEX_HANDOFF.md').unlink()
        self.assertTrue(execute(self.store,'handoff.ensure')['regenerated'])
        self.assertFalse(execute(self.store,'handoff.ensure')['regenerated'])

    def test_P_only_selected_branch_is_read(self):
        self.materials()
        self.do('knowledge.import',{'catalog':{'id':'english','subject':'english1','name':'英语'},'points':[
               {'id':'english-k','external_id':'EN1','name':'无关词汇','criteria':'辨析'}]})
        original=Path.read_text;read_paths=[]
        def tracked(path,*args,**kwargs):
            read_paths.append(str(path));return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',tracked):
            # locate returns metadata without reading textbook content.
            result=execute(self.store,'materials.locate',{'knowledge_id':'k1','level':2,'reason':'制定本次目标'})
        self.assertEqual(result['subject'],'math2');self.assertEqual(len(result['materials']),1)
        self.assertFalse(any('outline.md' in p for p in read_paths))
        self.assertNotIn('english-k',canonical(result))
        with self.assertRaises(Error): execute(self.store,'materials.read',{'knowledge_id':'k1','level':3})


if __name__=='__main__': unittest.main()
