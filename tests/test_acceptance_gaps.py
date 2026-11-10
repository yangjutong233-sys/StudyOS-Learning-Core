"""Completion-audit regressions not exercised by the original green suite."""
import json
from studyos.app import execute
from studyos.common import Error
from tests.support import Case


class CompletionAuditTests(Case):
    def test_gpt_packet_lists_markdown_upload_with_bounded_location(self):
        self.lesson()
        with self.store.connect(True) as c:
            pinned=c.execute("SELECT data FROM lesson_tasks WHERE id='t1'").fetchone()[0]
        self.assertIn('outline',pinned)
        body=(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn('能解释并应用',body)
        self.assertIn('本次Markdown课件（需上传）',body)
        self.assertNotIn('无需向网页重复上传',body)
        navigation=json.loads(body.split('## 本次Markdown课件（需上传）\n\n',1)[1].split('\n\n',1)[0])
        self.assertEqual(navigation[0]['用户查找路径'],str(self.root/'synthetic_materials/outline.md'))
        self.assertEqual(navigation[0]['定位'],{'line_start':1,'line_end':3})
        self.assertEqual(len(navigation[0]['sha256']),64)
        self.assertIn('需上传',navigation[0]['上传要求'])
        self.assertNotIn('根目录兼容快照',body)
        attachments=body.split('## 新窗口教材附件清单',1)[1].split('\n## ',1)[0]
        self.assertNotIn('outline.md',attachments)
        self.assertIn('outline.md',body)

    def test_assessment_and_review_have_human_readable_archived_reports(self):
        first,_=self.assessment(at='2026-01-01')
        with self.store.connect(True) as c:
            artifact=c.execute("SELECT * FROM artifacts WHERE kind='assessments' AND entity_id=?",(first['id'],)).fetchone()
            self.assertIsNotNone(artifact)
            text=(self.root/artifact['path']).read_text(encoding='utf-8')
            self.assertIn('合成实际原答',text);self.assertIn('合成评分依据',text)
            review=dict(c.execute("SELECT * FROM review_tasks WHERE status='PENDING' ORDER BY rowid").fetchone())
        later,_=self.assessment(at='2026-01-02')
        self.do('review.complete',{'id':review['id'],'expected_revision':1,'assessment_id':later['id']},at='2026-01-02')
        with self.store.connect(True) as c:
            artifact=c.execute("SELECT * FROM artifacts WHERE kind='reviews' AND entity_id=?",(review['id'],)).fetchone()
            self.assertIsNotNone(artifact)
            text=(self.root/artifact['path']).read_text(encoding='utf-8')
            self.assertIn('2026-01-05',text);self.assertIn(later['id'],text)
        self.assertTrue(execute(self.store,'doctor')['ok'])

    def test_mistake_original_and_correction_reports_preserve_history(self):
        _,bad=self.assessment('FAIL',at='2026-01-01')
        self.do('mistake.record-event',{'id':bad['mistake_id'],'expected_revision':1,
                    'action':'UNDERSTOOD','reason':'已解释错误条件，但尚未独立掌握'})
        with self.store.connect(True) as c:
            artifacts=c.execute("SELECT * FROM artifacts WHERE kind='mistakes' AND entity_id=? ORDER BY version",(bad['mistake_id'],)).fetchall()
            self.assertEqual(len(artifacts),2)
            self.assertIn('合成实际原答',(self.root/artifacts[0]['path']).read_text(encoding='utf-8'))
            self.assertIn('尚未独立掌握',(self.root/artifacts[1]['path']).read_text(encoding='utf-8'))

    def attempt(self,question,answer='原始答案',assistance='NONE',assessment_id=None,kid='k1'):
        return self.do('attempt.add',{'knowledge_id':kid,'question':question,'answer':answer,'process':'原始过程',
             'form':'recall','dimensions':['concept'],'source':'codex_independent','confirmed':True,'occurred_at':self.store.at,
             'assistance':assistance,'assessment_id':assessment_id,
             'evaluation':{'result':'PASS','basis':'合成标准','reference':'合成参考'}})

    def test_seen_question_plus_new_question_cannot_count_as_two_independent_questions(self):
        self.attempt('已暴露原题',assistance='ANSWER_SEEN')
        a=self.do('assessment.create',{'knowledge_id':'k1','blueprint':{'criteria':'两种独立提取','rationale':'需要两题独立证据',
              'min_attempts':2,'forms':['recall'],'dimensions':['concept']}})
        self.attempt('已暴露原题',assessment_id=a['id'])
        self.attempt('从未见过的新题',assessment_id=a['id'])
        result=self.do('assessment.submit',{'id':a['id'],'expected_revision':1})
        self.assertEqual(result['result'],'HINTED')
        self.assertNotEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'MASTERED')

    def test_handoff_bounds_history_and_keeps_explicit_old_evidence(self):
        records=[self.attempt(f'历史题{i:03d}') for i in range(35)]
        self.attempt('其他知识点不应出现',kid='k2')
        self.materials()
        self.do('lesson.create',{'id':'scoped','knowledge_id':'k1','goal':'复核当前概念','strategy':'对比最近表现与早期表述',
              'acceptance':'独立解释','next_task':'针对当前概念练习',
              'context_attempt_ids':[records[0]['id']],'context_reason':'核对最早概念表述'})
        self.do('lesson.prepare',{'task_id':'scoped'})
        body=(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn('历史题000',body);self.assertIn('历史题034',body)
        self.assertNotIn('历史题001',body);self.assertNotIn('其他知识点不应出现',body)
        self.assertIn('核对最早概念表述',body)
        self.assertIn('历史范围',body)
        self.assertEqual(self.count('attempts'),36)

    def test_explicit_context_requires_reason_and_matching_knowledge(self):
        other=self.attempt('其他点的题',kid='k2')
        raw={'id':'scoped','knowledge_id':'k1','goal':'g','strategy':'s','acceptance':'a','next_task':'n',
             'context_attempt_ids':[other['id']]}
        with self.assertRaises(Error): self.do('lesson.create',raw)
        with self.assertRaises(Error): self.do('lesson.create',{**raw,'context_reason':'不应误带无关资料'})
        self.assertEqual(self.count('lesson_tasks'),0)
