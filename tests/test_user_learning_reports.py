import json
from studyos.app import execute
from studyos.common import Error
from studyos.reviews import first_learning
from tests import test_narrative_report as narrative
from tests.support import Case


class UserLearningReportTests(Case):
    fixture=narrative.NarrativeReportTests.fixture

    def test_saved_user_report_policy_makes_future_delivery_formal_by_default(self):
        raw=self.raw_formal();raw.pop('user_delivered')
        cfg=self.store.config('materials')
        cfg['report_policy']={'user_delivered_is_authentic':True,'formats':'ANY','independent_mastery_requires_test':True}
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'用户明确报告均为正式学习','config':cfg})
        self.assertEqual(self.do('report.receive',raw)['status'],'ACCEPTED')
        self.assertEqual(self.count('session_records'),1)
    def raw_formal(self,filename='user-report.txt',date='2026-01-01'):
        raw=self.fixture()
        path=self.root/filename
        path.write_bytes(b'%PDF\x00\xffuser-delivered-original' if path.suffix=='.pdf' else '用户确认刚学完；部分理解，未独立测试。'.encode('utf-8'))
        raw.pop('markdown_path')
        raw.update({'source_path':str(path),'user_delivered':True,'reported_date':date})
        return raw

    def test_date_only_user_report_is_a_formal_learning_record_without_invented_answers(self):
        raw=self.raw_formal();result=self.do('report.receive',raw)
        self.assertEqual(result['status'],'ACCEPTED')
        self.assertEqual(result['time_precision'],'DATE_ONLY')
        self.assertEqual(self.count('learning_sessions'),1)
        self.assertEqual(self.count('session_records'),1)
        for table in ('attempts','assessments','mastery_assessments','daily_plans'):
            self.assertEqual(self.count(table),0)
        self.assertEqual(self.count('review_series'),1)
        self.assertEqual(self.count('review_tasks'),1)
        with self.store.connect(True) as c:
            self.assertEqual(c.execute('SELECT started_at FROM learning_sessions').fetchone()[0],'2026-01-01')
            row=c.execute('SELECT * FROM session_records').fetchone()
            self.assertEqual(row['occurred_at'],'2026-01-01');self.assertIsNone(row['minutes'])
            self.assertIsNone(json.loads(row['data'])['actual_started_at'])
            series=c.execute('SELECT * FROM review_series').fetchone()
            task=c.execute('SELECT * FROM review_tasks').fetchone()
            self.assertEqual(series['kind'],'knowledge');self.assertEqual(series['knowledge_id'],'k1')
            self.assertEqual(task['due_date'],'2026-01-02');self.assertEqual(task['status'],'PENDING')
            self.assertEqual(result['review']['task_id'],task['id'])
            self.store.c=c
            try:self.assertEqual(first_learning(self.store,['k1'])['evidence'][0]['actual_at'],'2026-01-01')
            finally:self.store.c=None
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'ASSESSMENT_PENDING')
        for filename in ('GPT_TEACHING_HANDOFF.md','CODEX_HANDOFF.md'):
            text=(self.root/'subjects/linear'/filename).read_text(encoding='utf-8')
            if filename=='CODEX_HANDOFF.md':self.assertIn('正式学习回传数',text)
            else:self.assertNotIn('正式学习回传数',text)
            self.assertIn('第一轮初步学习，部分理解',text)
        self.assertEqual(self.do('report.receive',raw)['artifact_id'],result['artifact_id'])
        self.assertEqual(self.count('session_records'),1)
        with self.assertRaises(Error) as caught:
            self.do('assessment.create',{'knowledge_id':'k1','blueprint':{'criteria':'独立解释','rationale':'延迟测试',
                'min_attempts':1,'forms':['recall'],'dimensions':['concept'],'delay_days':1,
                'delay_anchor':{'entity_type':'session_record','entity_id':result['session_record_id']}}})
        self.assertEqual(caught.exception.code,'EVIDENCE_TIME_UNVERIFIED')
        self.assertEqual(execute(self.store,'doctor')['issues'],[])

    def test_formal_report_with_date_creates_first_review_once(self):
        raw=self.raw_formal();first=self.do('report.receive',raw)
        self.assertEqual(self.count('review_series'),1)
        self.assertEqual(self.count('review_tasks'),1)
        self.assertEqual(first['review']['due_date'],'2026-01-02')
        second=self.do('report.receive',raw)
        self.assertEqual(second['artifact_id'],first['artifact_id'])
        self.assertEqual(self.count('review_series'),1)
        self.assertEqual(self.count('review_tasks'),1)

    def test_one_report_can_mark_multiple_covered_points(self):
        raw=self.raw_formal();raw['knowledge_ids']=['k1','k2']
        result=self.do('report.receive',raw)
        self.assertEqual(result['knowledge_ids'],['k1','k2'])
        self.assertEqual(self.count('learning_sessions'),1)
        self.assertEqual(self.count('review_series'),2)
        self.assertEqual(self.count('review_tasks'),2)
        self.assertEqual(len(result['reviews']),2)
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'ASSESSMENT_PENDING')
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k2'})['status'],'ASSESSMENT_PENDING')

    def test_session_duration_is_appended_without_overwriting_original_batch(self):
        raw=self.raw_formal();result=self.do('report.receive',raw)
        fixed=self.do('session.duration',{'session_id':result['session_id'],'minutes':45,
            'reason':'用户手动补录','expected_revision':1})
        self.assertEqual(fixed['minutes'],45)
        summary=execute(self.store,'summary')
        self.assertEqual(summary['learning_time']['confirmed_minutes'],45)
        self.assertEqual(summary['learning_time']['unknown_batches'],0)
        self.assertEqual(self.count('session_records'),2)

    def test_unknown_time_remains_unknown_and_does_not_anchor_review(self):
        result=self.do('report.receive',self.raw_formal(date=None))
        self.assertEqual(result['time_precision'],'UNKNOWN')
        self.assertEqual(self.count('review_series'),0)
        self.assertEqual(self.count('review_tasks'),0)
        with self.store.connect(True) as c:
            self.assertEqual(c.execute('SELECT started_at FROM learning_sessions').fetchone()[0],'')
            self.store.c=c
            try:self.assertIsNone(first_learning(self.store,['k1']))
            finally:self.store.c=None

    def test_binary_format_is_preserved_and_does_not_require_a_companion_json(self):
        raw=self.raw_formal('user-report.pdf');result=self.do('report.receive',raw)
        shown=execute(self.store,'report.show',{'id':result['artifact_id']})
        self.assertIsNone(shown['text'])
        self.assertTrue(shown['path'].endswith('.pdf'))
        from pathlib import Path
        self.assertEqual(Path(shown['path']).read_bytes(),Path(raw['source_path']).read_bytes())
        self.assertEqual(result['status'],'ACCEPTED')

    def test_prior_pending_intake_is_corrected_by_append_without_duplicate_learning(self):
        raw=self.fixture();old=self.do('report.receive',raw)
        confirm={'id':old['artifact_id'],'user_delivered':True,'authorization_ref':'用户确认是刚学完的真实报告'}
        new=self.do('report.confirm-learning',confirm)
        self.assertEqual(new['status'],'ACCEPTED')
        self.assertEqual(execute(self.store,'report.show',{'id':old['artifact_id']})['artifact']['status'],'SUPERSEDED')
        self.assertEqual(execute(self.store,'report.show',{'id':old['artifact_id']})['text'],execute(self.store,'report.show',{'id':new['artifact_id']})['text'])
        self.assertTrue(self.do('report.confirm-learning',confirm)['duplicate_report'])
        self.assertEqual(self.count('session_records'),1)
        self.assertEqual(execute(self.store,'summary')['pending_reports'],[])

    def test_unassigned_user_report_is_formal_then_can_be_associated_without_guessing(self):
        raw=self.raw_formal(date=None);raw.pop('knowledge_id')
        result=self.do('report.receive',raw)
        self.assertEqual(result['status'],'ACCEPTED')
        self.assertEqual(result['association_status'],'UNASSIGNED')
        self.assertEqual(self.count('learning_sessions'),0)
        self.assertEqual(execute(self.store,'summary')['learning_reports']['unassigned_user_reports'],1)
        associated=self.do('report.confirm-learning',{'id':result['artifact_id'],'user_delivered':True,
            'knowledge_id':'k1','authorization_ref':'根据原报告补充准确知识点关联'})
        self.assertEqual(associated['status'],'ACCEPTED')
        self.assertEqual(self.count('session_records'),1)
