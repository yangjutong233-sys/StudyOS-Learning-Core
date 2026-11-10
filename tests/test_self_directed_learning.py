import json
from studyos.app import execute
from studyos.common import Error, canonical
from studyos.reports import markdown_template
from tests.support import Case


class SelfDirectedLearningTests(Case):
    def enable(self):
        cfg=self.store.config('materials')
        cfg['learning_control']={'daily_planning':'USER_ONLY','gpt_handoff':'PROGRESS_ONLY'}
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'用户自行决定每日学习','config':cfg})

    def packet(self,name='GPT_TEACHING_HANDOFF.md'):
        return (self.root/'subjects/linear'/name).read_text(encoding='utf-8')

    def import_actual(self,session,task,kid,tag,external=None):
        data={'report_id':'report-'+tag,'version':1,'task_id':task,'task_version':1,
              'knowledge_external_id':external or ('LA-TEST-01' if kid=='k1' else 'LA-TEST-02'),
              'session_id':session['session_id'],'covered':['用户实际选择的内容-'+tag],
              'checkpoint':'真实断点-'+tag,'minutes':None,'unfinished_training':[],
              'attempts':[{'id':'answer-'+tag,'question':'真实题目-'+tag,'answer':'原答-'+tag,
                  'process':'原过程','form':'application','dimensions':['concept'],'assistance':'LIGHT_HINT',
                  'answer_seen':False,'evaluation':{'result':'PARTIAL','basis':'合成真实教学标准',
                      'reference':'对应大纲','error_type':'CONCEPT','error_reason':'尚待核验'}}]}
        md=self.root/('report-'+tag+'.md');jp=self.root/('report-'+tag+'.json')
        md.write_text(markdown_template(data),encoding='utf-8');jp.write_text(canonical(data),encoding='utf-8')
        return self.do('report.import',{'markdown_path':str(md),'json_path':str(jp),'semantic_review_ref':'合成测试逐点核验'})

    def test_unstarted_packet_has_no_daily_assignment_even_when_draft_contains_limits(self):
        self.materials()
        self.do('lesson.create',{'id':'draft','knowledge_id':'k1','goal':'DAILY_GOAL',
            'strategy':'DAILY_STRATEGY：上午只学5页60分钟','acceptance':'DAILY_ACCEPTANCE','next_task':'DAILY_NEXT'})
        self.do('lesson.prepare',{'task_id':'draft'})
        self.enable()
        packet=self.packet()
        for forbidden in ('DAILY_GOAL','DAILY_STRATEGY','DAILY_ACCEPTANCE','DAILY_NEXT',
                          '## 学习目标','## 当前教学策略','## 下一步教学任务','## 完成标准','只讲当前任务'):
            self.assertNotIn(forbidden,packet)
        self.assertIn('学习内容、顺序和学习量由用户决定',packet)
        self.assertEqual(self.count('learning_sessions'),0)

    def test_no_task_has_only_a_short_progress_note(self):
        self.enable()
        self.assertIn('暂无已登记的学习进度',self.packet())
        self.assertNotIn('task_id',self.packet())
        self.assertNotIn('起始导航',self.packet())

    def test_multiple_actual_point_reports_refresh_both_handoffs_without_a_daily_plan(self):
        first=self.lesson();self.enable()
        self.assertEqual(self.import_actual(first,'t1','k1','first')['status'],'ACCEPTED')
        self.do('materials.register',{'id':'outline','expected_revision':1,'subject':'math2','path_key':'outline',
            'role':'outline','bindings':[{'knowledge_id':'k2','locator':{'line_start':1,'line_end':3}}]})
        self.do('lesson.create',{'id':'actual-second','knowledge_id':'k2','goal':'关联实际第二点',
            'strategy':'依据实际回传','acceptance':'对应大纲','next_task':'用户自行决定'})
        second=self.do('lesson.start',{'task_id':'actual-second'})
        self.assertEqual(self.import_actual(second,'actual-second','k2','second')['status'],'ACCEPTED')
        packet=self.packet()
        for value in ('真实断点-second','LA-TEST-01','LA-TEST-02'):
            self.assertIn(value,packet)
        self.assertNotIn('原答-first',packet)
        self.assertEqual(self.count('attempts'),2)
        self.assertIn('真实断点-second',self.packet('CODEX_HANDOFF.md'))
        self.assertEqual(self.count('daily_plans'),0)
        self.assertEqual(self.count('session_records'),2)
        self.assertEqual(self.count('mastery_assessments'),0)

    def test_mismatched_report_point_is_pending_without_changing_progress(self):
        session=self.lesson();self.enable()
        result=self.import_actual(session,'t1','k1','wrong',external='LA-TEST-02')
        self.assertEqual(result['status'],'PENDING')
        self.assertIn('knowledge_external_id',result['differences'])
        self.assertEqual(self.count('attempts'),0)
        self.assertEqual(self.count('session_records'),0)

    def test_daily_plan_requires_user_provided_content_and_explicit_reference(self):
        self.enable();self.confirm()
        ctx=execute(self.store,'planning-context')
        plan={'id':'user-plan','day':self.store.at[:10],'schedule_hash':ctx['capacity']['schedule_hash'],
              'items':[{'id':'one','knowledge_id':'k1','start':'08:00','end':'08:30',
                        'goal':'用户选定','reason':'用户选定','acceptance':'用户选定'}]}
        with self.assertRaises(Error) as caught:self.do('plan.apply',plan)
        self.assertEqual(caught.exception.code,'DAILY_PLANNING_USER_ONLY')
        self.assertEqual(self.count('daily_plans'),0)
        self.assertEqual(self.do('plan.apply',{**plan,'source':'user_provided',
            'authorization_ref':'合成用户明确给出内容与时间'})['version'],1)
