import copy
import json
from tests.support import Case
from studyos.common import Error


ENGLISH={'basis':'first_learning_date','intervals':[1,3,7,14,30],
         'failure_days':1,'partial_days':1,'recovery_passes':2}


class EnglishReviewTests(Case):
    def setUp(self):
        super().setUp()
        self.do('knowledge.import',{'catalog':{'id':'english','subject':'english1','name':'合成英语课件'},
            'points':[{'id':'en','external_id':'EN-1','name':'合成词汇','criteria':'独立提取'},
                      {'id':'en2','external_id':'EN-2','name':'另一批','criteria':'语境应用'}]})

    def enable(self):
        cfg=self.store.config('review_policy');cfg['subject_overrides']={'english1':copy.deepcopy(ENGLISH)}
        self.do('policy.update',{'name':'review_policy','config':cfg,'expected_revision':cfg['revision'],'reason':'合成用户选择英语课件复习'})

    def answer(self,at,result='PASS',kid='en'):
        return self.assessment(at=at,result=result,kid=kid,dimensions=['vocabulary'])[0]

    def initial(self):
        self.answer('2026-01-01')
        with self.store.connect(True) as c:
            return dict(c.execute("select t.* from review_tasks t join review_series s on s.id=t.series_id where s.kind='knowledge' and s.knowledge_id='en'").fetchone())

    def finish(self,t,at,result='PASS',revision=1,**extra):
        a=self.answer(at,result)
        return self.do('review.complete',{'id':t['id'],'expected_revision':revision,'assessment_id':a['id'],**extra},at=at)

    def test_all_offsets_and_terminal_d30(self):
        self.enable();self.confirm(all_days=True);t=self.initial()
        for actual,next_day in [('2026-01-02','2026-01-04'),('2026-01-04','2026-01-08'),
                                ('2026-01-08','2026-01-15'),('2026-01-15','2026-01-31'),('2026-01-31',None)]:
            done=self.finish(t,actual)
            if next_day:
                self.assertEqual(done['next']['suggested_date'],next_day);t=done['next']
            else:self.assertIsNone(done['next'])
        self.assertEqual(done['stage'],5)
        report=self.do('report.show',{'id':done['report_artifact_id']})
        self.assertIn('不自动追加D60/D90',str(report))

    def test_late_completion_and_rest_day_do_not_shift_d0(self):
        self.enable();self.confirm();t=self.initial()
        done=self.finish(t,'2026-01-03')
        self.assertEqual(done['next']['suggested_date'],'2026-01-04')
        self.assertEqual(done['next']['due_date'],'2026-01-05')
        done=self.finish(done['next'],'2026-01-05')
        self.assertEqual(done['next']['suggested_date'],'2026-01-08')

    def test_failure_short_recovery_requires_consecutive_successes_and_preserves_anchor(self):
        self.enable();self.confirm(all_days=True);t=self.initial()
        failed=self.finish(t,'2026-01-02','FAIL')
        self.assertEqual(failed['stage'],0);self.assertEqual(failed['next']['suggested_date'],'2026-01-03')
        first=self.finish(failed['next'],'2026-01-03')
        self.assertEqual(first['stage'],0);self.assertEqual(first['next']['suggested_date'],'2026-01-04')
        recovered=self.finish(first['next'],'2026-01-04')
        self.assertEqual(recovered['stage'],1)
        self.assertEqual(recovered['next']['suggested_date'],'2026-01-04')

    def test_other_subjects_and_existing_series_keep_old_algorithm(self):
        old=self.do('review.create',{'kind':'knowledge','knowledge_id':'en','reason':'旧规则系列'})
        self.enable()
        new=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'数学不变'})
        a=self.answer('2026-01-03')
        done=self.do('review.complete',{'id':old['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-03')
        self.assertEqual(done['next']['suggested_date'],'2026-01-06')
        a,_=self.assessment(at='2026-01-03')
        done=self.do('review.complete',{'id':new['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-03')
        self.assertEqual(done['next']['suggested_date'],'2026-01-06')

    def test_missing_actual_learning_is_pending_and_cannot_be_replaced_by_record_date(self):
        self.enable()
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'en','reason':'无真实作答'})
        with self.store.connect(True) as c:
            sd=json.loads(c.execute('select data from review_series where id=?',(s['id'],)).fetchone()[0])
        self.assertTrue(sd['anchor_pending']);self.assertIsNone(sd['learning_anchor'])
        a=self.answer('2026-01-05')
        with self.assertRaises(Error) as caught:
            self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-05')
        self.assertEqual(caught.exception.code,'EVIDENCE_TIME_INVALID')
        done=self.finish(s['task'],'2026-01-06')
        self.assertEqual(done['next']['suggested_date'],'2026-01-08')

    def test_undetermined_keeps_round_and_reset_cannot_reanchor(self):
        self.enable();t=self.initial()
        pending=self.finish(t,'2026-01-02','UNDETERMINED')
        self.assertIsNone(pending['completed_at']);self.assertEqual(pending['stage'],0)
        with self.assertRaises(Error):self.finish(t,'2026-01-03',revision=2,reset=True,reason='不能改首次日期')
        done=self.finish(t,'2026-01-04',revision=2)
        self.assertEqual(done['next']['suggested_date'],'2026-01-04')

    def test_pending_recovery_keeps_consecutive_success_requirement(self):
        self.enable();t=self.initial()
        failed=self.finish(t,'2026-01-02','FAIL')
        retry=failed['next']
        self.finish(retry,'2026-01-03','UNDETERMINED')
        done=self.finish(retry,'2026-01-04',revision=2)
        self.assertEqual(done['stage'],0)
        self.assertEqual(done['next']['suggested_date'],'2026-01-05')

    def test_pending_date_resolution_keeps_audit_and_task_revision_consistent(self):
        self.enable();self.confirm(all_days=True)
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'en','reason':'等待实际首次学习'})
        with self.assertRaises(Error):
            self.do('review.start',{'id':s['task']['id'],'expected_revision':1,'reason':'无依据不能开始'})
        self.answer('2026-01-05')
        r=self.do('review.reschedule',{'id':s['task']['id'],'expected_revision':1,'due_date':'2026-01-07','reason':'确认实际学习后安排'},at='2026-01-05')
        self.assertEqual(r['revision'],2)
        done=self.finish(s['task'],'2026-01-07',revision=2)
        self.assertEqual(done['next']['suggested_date'],'2026-01-08')

    def test_mixed_algorithms_and_invalid_policy_are_rejected(self):
        self.enable()
        with self.assertRaises(Error) as caught:
            self.do('review.create',{'kind':'comprehensive','members':['en','k1'],'reason':'算法不同'})
        self.assertEqual(caught.exception.code,'REVIEW_POLICY_MISMATCH')
        cfg=self.store.config('review_policy');cfg['subject_overrides']['english1']['intervals']=[1,1,3]
        with self.assertRaises(Error):
            self.do('policy.update',{'name':'review_policy','config':cfg,'expected_revision':2,'reason':'非法重复偏移'})
