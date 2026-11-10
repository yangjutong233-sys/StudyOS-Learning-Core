import json
from studyos.app import execute
from studyos.handoff import progress_sources
from tests.support import Case


class CompactHandoffTests(Case):
    def test_large_curated_and_fallback_packets_are_bounded_without_preferences(self):
        self.setup_progress()
        cfg=self.store.config('materials')
        cfg['teaching_preferences']={'linear':['不应上传的偏好']}
        with self.store.connect(True) as c:_,sources=progress_sources(c,['k1','k2'])
        cfg['web_progress_summaries']={'linear':{'sources':sources,
            'preference_source':['不应上传的偏好'],'teaching_preferences':['不应上传的偏好'],
            'sections':{name:['内容'*1000]*50 for name in
                ('已学内容','学习断点','理解情况','尚未确认','已纠正的误区')}}}
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'验证长摘要','config':cfg})
        packet=self.packet()
        self.assertLess(len(packet),1200)
        self.assertNotIn('教学偏好',packet)
        self.assertNotIn('不应上传的偏好',packet)
        self.assertIn('学习断点',packet)
        self.assertIn('…',packet)
        self.do('lesson.pause',{'session_id':self.session_id,'expected_revision':2,
            'checkpoint':'新断点'+'实际内容'*1000,'covered':['基础']})
        self.assertLess(len(self.packet()),1200)
        self.assertIn('新断点',self.packet())
        self.assertNotIn('教学偏好',self.packet())

    def setup_progress(self):
        session=self.lesson()
        self.session_id=session['session_id']
        self.do('lesson.pause',{'session_id':session['session_id'],'expected_revision':1,
            'checkpoint':'实际断点一','covered':['已讲概念']})
        cfg=self.store.config('materials')
        cfg['learning_control']={'daily_planning':'USER_ONLY','gpt_handoff':'PROGRESS_ONLY'}
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'网页版只需学习摘要','config':cfg})
        return session

    def packet(self):
        return (self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')

    def save_summary(self):
        with self.store.connect(True) as c:_,sources=progress_sources(c,['k1','k2'])
        cfg=self.store.config('materials')
        cfg['web_progress_summaries']={'linear':{'sources':sources,'preference_source':[],
            'sections':{'已学内容':'精简的已学概念','理解情况':'课堂引导下理解，尚无独立测试结论。'},
            'teaching_preferences':[]}}
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'按真实记录保存简短教学摘要','config':cfg})

    def test_web_packet_is_short_plain_markdown_and_freshness_still_works(self):
        self.setup_progress();self.save_summary()
        header,body=self.packet().split('\n',1)
        meta=json.loads(header[13:-4])
        self.assertEqual(set(meta),{'freshness_key','snapshot_id','content_sha256'})
        self.assertLess(len(body),400)
        for text in ('科目资料导航','网页版材料','教学交接','task_id','records/','studyos','```','JSON','成果回传'):
            self.assertNotIn(text,body)
        self.assertIn('精简的已学概念',body)
        self.assertTrue(execute(self.store,'handoff.show',{'id':'linear','target':'gpt'})['fresh'])
        before=self.count('audit_events')
        path=self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md'
        path.write_text(self.packet().replace('精简的已学概念','错误内容'),encoding='utf-8')
        self.assertFalse(execute(self.store,'handoff.show',{'id':'linear','target':'gpt'})['fresh'])
        self.assertTrue(execute(self.store,'handoff.ensure',{'target':'both'})['regenerated'])
        self.assertIn('精简的已学概念',self.packet())
        self.assertEqual(self.count('audit_events'),before)

    def test_new_real_learning_invalidates_the_curated_summary(self):
        session=self.setup_progress();self.save_summary()
        self.do('lesson.pause',{'session_id':session['session_id'],'expected_revision':2,
            'checkpoint':'实际断点二','covered':['继续实际学习']})
        self.assertNotIn('精简的已学概念',self.packet())
        self.assertIn('实际断点二',self.packet())
        self.assertEqual(self.count('session_records'),2)

    def test_independent_test_result_updates_the_brief_instead_of_reusing_an_old_claim(self):
        self.setup_progress();self.save_summary()
        self.assessment('PASS')
        self.assertNotIn('精简的已学概念',self.packet())
        self.assertIn('已通过独立掌握测试',self.packet())
