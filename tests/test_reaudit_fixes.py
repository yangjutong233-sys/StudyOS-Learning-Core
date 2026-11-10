"""Second-audit regressions. All learning records are synthetic and isolated."""
import copy
import json
from studyos.app import execute
from studyos.common import Error, uid
from tests.support import Case


class ReauditFixTests(Case):
    def blueprint(self, at, **extra):
        return self.do('assessment.create', {'knowledge_id':'k1','blueprint':{
            'criteria':'预先固定条件','rationale':'隔离复审','min_attempts':1,
            'forms':['recall'],'dimensions':['concept'],**extra}},at=at)

    def answer(self, assessment, recorded, actual, result='PASS'):
        raw={'knowledge_id':'k1','assessment_id':assessment['id'],'question':'新题 '+uid(),
            'answer':'原答','process':'原过程','form':'recall','dimensions':['concept'],
            'source':'codex_independent','confirmed':True,'assistance':'NONE',
            'evaluation':{'result':result,'basis':'固定标准','reference':'参考答案'}}
        if actual is not None:raw['occurred_at']=actual
        return self.do('attempt.add',raw,at=recorded)

    def test_RA01_old_answer_cannot_reverse_recent_failure(self):
        self.assessment('FAIL',at='2026-01-09')
        a=self.blueprint('2026-01-10')
        original=self.answer(a,'2026-01-10','2026-01-01')
        before=self.count('mastery_assessments')
        with self.assertRaises(Error) as rejected:
            self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-10')
        self.assertEqual(rejected.exception.code,'EVIDENCE_TIME_INVALID')
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'CONSOLIDATE')
        self.assertEqual(self.count('mastery_assessments'),before)
        self.assertIn(original['id'],[r['id'] for r in execute(self.store,'attempt.list')])
        self.assertEqual(execute(self.store,'assessment.show',{'id':a['id']})['status'],'OPEN')

    def plan(self, review_id, day='2026-01-03', pid='supplement', iid='supplement-item'):
        ctx=execute(self.store,'planning-context',{'day':day})
        return {'id':pid,'day':day,'schedule_hash':ctx['capacity']['schedule_hash'],'items':[
            {'id':iid,'knowledge_id':'k1','review_id':review_id,'start':'08:00','end':'09:00',
             'goal':'补证据','reason':'无法判定后的独立验证','acceptance':'无提示通过'}]}

    def pending_review(self):
        self.confirm(all_days=True)
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'等待补证据'})
        a,_=self.assessment('UNDETERMINED',at='2026-01-02')
        self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-02')
        return s['task']['id']

    def test_RA02_supplement_can_be_planned_and_completed(self):
        rid=self.pending_review();p=self.plan(rid)
        self.assertTrue(execute(self.store,'plan.validate',p)['valid'])
        applied=self.do('plan.apply',p,at='2026-01-02')
        self.assertEqual(applied['version'],1)
        a,_=self.assessment(at='2026-01-03')
        done=self.do('review.complete',{'id':rid,'expected_revision':2,'assessment_id':a['id']},at='2026-01-03')
        self.assertEqual(done['next']['suggested_date'],'2026-01-06')
        self.do('plan.record-progress',{'plan_id':p['id'],'id':p['items'][0]['id'],
            'expected_revision':1,'status':'COMPLETED','reason':'补证据完成'},at='2026-01-03')
        self.assertEqual(execute(self.store,'plan.show',{'id':p['id']})['items'][0]['status'],'COMPLETED')
        with self.store.connect(True) as c:
            self.assertEqual(c.execute('SELECT status FROM review_tasks WHERE id=?',(rid,)).fetchone()[0],'COMPLETED')
        self.do('plan.revise',{**p,'expected_revision':1},at='2026-01-03')
        self.assertEqual(execute(self.store,'plan.show',{'id':p['id']})['items'][0]['status'],'COMPLETED')

    def test_unknown_time_cannot_prove_predeclared_blueprint(self):
        a=self.blueprint('2026-01-02')
        self.answer(a,'2026-01-03',None)
        with self.assertRaises(Error) as rejected:
            self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-03')
        self.assertEqual(rejected.exception.code,'EVIDENCE_TIME_UNVERIFIED')
        self.assertEqual(self.count('mastery_assessments'),0)

    def test_exact_creation_boundary_and_late_recording_are_valid(self):
        a=self.blueprint('2026-01-02T09:00:00.500000+08:00')
        self.answer(a,'2026-01-05','2026-01-02T01:00:00.500000+00:00')
        done=self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-05')
        self.assertEqual(done['result'],'PASS')

    def test_delayed_anchor_does_not_allow_preblueprint_answer(self):
        initial,_=self.assessment(at='2026-01-01')
        a=self.blueprint('2026-01-10',delay_days=1,
            delay_anchor={'entity_type':'assessment','entity_id':initial['id']})
        self.answer(a,'2026-01-10','2026-01-03')
        with self.assertRaises(Error) as rejected:
            self.do('assessment.submit',{'id':a['id'],'expected_revision':1},at='2026-01-10')
        self.assertEqual(rejected.exception.code,'EVIDENCE_TIME_INVALID')

    def test_genuine_old_blueprint_cannot_reverse_newer_evaluation(self):
        old=self.blueprint('2026-01-01')
        self.assessment('FAIL',at='2026-01-09')
        self.answer(old,'2026-01-10','2026-01-02')
        with self.assertRaises(Error) as rejected:
            self.do('assessment.submit',{'id':old['id'],'expected_revision':1},at='2026-01-10')
        self.assertEqual(rejected.exception.code,'EVIDENCE_STALE')
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'CONSOLIDATE')

    def test_supplement_duplicate_bindings_rejected_and_deferral_allows_move(self):
        rid=self.pending_review();p=self.plan(rid);self.do('plan.apply',p)
        duplicate=self.plan(rid,day='2026-01-04',pid='next',iid='next-item')
        with self.assertRaises(Error) as rejected:self.do('plan.apply',duplicate)
        self.assertEqual(rejected.exception.code,'DUPLICATE_REVIEW')
        self.do('plan.record-progress',{'plan_id':p['id'],'id':p['items'][0]['id'],
            'expected_revision':1,'status':'DEFERRED','reason':'延至次日补证据'})
        self.assertEqual(self.do('plan.apply',duplicate)['version'],1)
        self.assertEqual(self.do('plan.revise',{**p,'expected_revision':1})['version'],2)
        self.assertEqual(execute(self.store,'plan.show',{'id':p['id']})['items'][0]['status'],'DEFERRED')
        repeated=copy.deepcopy(duplicate);repeated['items'].append({**repeated['items'][0],'id':'other','start':'09:00','end':'10:00'})
        with self.assertRaises(Error):execute(self.store,'plan.validate',repeated)

    def test_ordinary_active_and_cancelled_reviews_not_newly_plannable(self):
        self.confirm(all_days=True)
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'正在执行'})
        rid=s['task']['id']
        self.do('review.start',{'id':rid,'expected_revision':1,'reason':'实际开始'})
        with self.assertRaises(Error):execute(self.store,'plan.validate',self.plan(rid))
        self.do('review.cancel',{'id':rid,'expected_revision':2,'reason':'取消'})
        with self.assertRaises(Error):execute(self.store,'plan.validate',self.plan(rid))

    def test_existing_active_plan_binding_can_be_preserved(self):
        self.confirm(all_days=True)
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'原计划'})
        p=self.plan(s['task']['id']);self.do('plan.apply',p)
        self.do('review.start',{'id':s['task']['id'],'expected_revision':1,'reason':'开始原计划'})
        self.assertEqual(self.do('plan.revise',{**p,'expected_revision':1})['version'],2)
        with self.assertRaises(Error):self.do('plan.revise',{**p,'expected_revision':1})
