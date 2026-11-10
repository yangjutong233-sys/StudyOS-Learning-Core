import json
from studyos.app import execute
from studyos.common import Error
from studyos.maintenance import create_backup, restore_backup
from studyos.storage import Store
from tests.support import Case


class NarrativeReportTests(Case):
    def fixture(self):
        cfg=self.store.config('materials')
        cfg['learning_control']={'daily_planning':'USER_ONLY','gpt_handoff':'PROGRESS_ONLY'}
        cfg['handoff_scopes'].append({'id':'english','title':'英语','subject':'english1','directory':'subjects/english'})
        cfg['teaching_preferences']={'linear':['先讲解，再提一个问题。']}
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'合成教学偏好','config':cfg})
        path=self.root/'returned.md'
        path.write_text('# 原始回传\n初步学习，没有独立测试。\n保留原文与错误。\n',encoding='utf-8')
        return {'markdown_path':str(path),'knowledge_id':'k1','source_ref':'合成用户回传',
                'reported_date':'2026-01-01','reported_summary':'第一轮初步学习，部分理解，尚未完整验收',
                'reported_facts':{'covered':['已讲基础概念'],'guided_checks':['只有引导判断，无逐字原答'],
                                  'unverified':['独立应用未验证'],'reported_errors':['概念曾混淆']}}

    def test_narrative_is_pending_and_refreshes_both_scoped_handoffs_without_fake_learning(self):
        raw=self.fixture();result=self.do('report.receive',raw)
        self.assertEqual(result['status'],'PENDING')
        for table in ('learning_sessions','session_records','attempts','attempt_evaluations','assessments','mastery_assessments','daily_plans','review_tasks','lesson_tasks'):
            self.assertEqual(self.count(table),0,table)
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'UNASSESSED')
        for filename in ('CODEX_HANDOFF.md','GPT_TEACHING_HANDOFF.md'):
            text=(self.root/'subjects/linear'/filename).read_text(encoding='utf-8')
            if filename=='CODEX_HANDOFF.md':self.assertIn('第一轮初步学习，部分理解',text)
            else:self.assertIn('暂无已登记的学习进度',text)
            if filename=='CODEX_HANDOFF.md':self.assertIn('先讲解，再提一个问题',text)
            else:self.assertNotIn('先讲解，再提一个问题',text)
            self.assertNotIn('尚未实际学习；从0开始',text)
            other=(self.root/'subjects/english'/filename).read_text(encoding='utf-8')
            self.assertNotIn('第一轮初步学习，部分理解',other)
            self.assertNotIn('只有引导判断',other)
        original=execute(self.store,'report.show',{'id':result['artifact_id']})
        self.assertEqual(original['text'],(self.root/'returned.md').read_text(encoding='utf-8'))
        self.assertEqual(execute(self.store,'doctor')['issues'],[])

    def test_repeated_original_does_not_duplicate_intake_and_changed_claim_requires_correction(self):
        raw=self.fixture();first=self.do('report.receive',raw)
        repeat=self.do('report.receive',raw)
        self.assertTrue(repeat['duplicate_report'])
        self.assertEqual(first['artifact_id'],repeat['artifact_id'])
        before=self.count('artifacts')
        with self.assertRaises(Error):self.do('report.receive',{**raw,'reported_summary':'擅自改成掌握'})
        self.assertEqual(self.count('artifacts'),before)

    def test_wrong_task_association_is_rejected_without_a_report(self):
        raw=self.fixture()
        self.do('lesson.create',{'id':'other','knowledge_id':'k2','goal':'g','strategy':'s','acceptance':'a','next_task':'n'})
        before=self.count('artifacts')
        with self.assertRaises(Error):self.do('report.receive',{**raw,'task_id':'other'})
        self.assertEqual(self.count('artifacts'),before)

    def test_pending_original_survives_verified_backup_and_restore(self):
        raw=self.fixture();received=self.do('report.receive',raw)
        backup=create_backup(self.store)
        destination=self.root.parent/'restored-return'
        restore_backup(backup['path'],destination,self.root)
        restored=Store(destination)
        report=execute(restored,'report.show',{'id':received['artifact_id']})
        self.assertEqual(report['text'],(self.root/'returned.md').read_text(encoding='utf-8'))
        self.assertEqual(execute(restored,'doctor')['issues'],[])
