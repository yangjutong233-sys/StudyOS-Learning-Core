import json
from unittest.mock import patch
from tests.support import Case
from studyos.app import execute
from studyos.common import Error
from studyos.subject_handoffs import effective_scopes


class SubjectHandoffTests(Case):
    def selected_lesson_fixture(self):
        self.materials()
        self.do('materials.register',{'id':'outline','subject':'math2','path_key':'outline','role':'outline',
            'expected_revision':1,'bindings':[{'knowledge_id':k,'locator':{'line_start':1,'line_end':3}} for k in ('k1','k2')]})
        self.configure_scopes()
        for identifier,kid in [('first','k1'),('second','k2')]:
            self.do('lesson.create',{'id':identifier,'knowledge_id':kid,'goal':identifier,
                'strategy':identifier+'-strategy','acceptance':'标准','next_task':'等待实际学习'})

    def scoped_task_id(self):
        path=self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md'
        text=path.read_text(encoding='utf-8')
        return json.loads(text.split('\n',1)[0][13:-4])['task_id']

    def configure_scopes(self):
        cfg=self.store.config('materials')
        cfg['handoff_scopes']=[{'id':'linear','title':'合成线代','subject':'math2','catalog_id':'linear','directory':'subjects/linear'},
                               {'id':'english','title':'合成英语','subject':'english1','directory':'subjects/english'}]
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'合成分科交接','config':cfg})

    def test_zero_progress_and_repair_without_creating_learning(self):
        self.configure_scopes()
        path=self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md'
        self.assertIn('尚未实际学习',path.read_text(encoding='utf-8'))
        self.assertIn('尚无有效教学任务',path.read_text(encoding='utf-8'))
        self.assertEqual(self.count('learning_sessions'),0)
        self.assertFalse(execute(self.store,'handoff.ensure',{'target':'both'})['regenerated'])
        path.write_text('tampered',encoding='utf-8')
        self.assertTrue(execute(self.store,'handoff.ensure',{'target':'both'})['regenerated'])
        self.assertEqual(self.count('learning_sessions'),0)

    def test_progress_auto_refreshes_and_other_subject_has_no_answers(self):
        session=self.lesson()
        self.configure_scopes()
        self.do('lesson.pause',{'session_id':session['session_id'],'expected_revision':1,'checkpoint':'SCOPED_PRIVATE_CHECKPOINT','covered':['实际合成训练']})
        own=(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        other=(self.root/'subjects/english/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn('SCOPED_PRIVATE_CHECKPOINT',own)
        self.assertNotIn('SCOPED_PRIVATE_CHECKPOINT',other)
        self.assertNotIn('LA-TEST-01',other)
        self.assertEqual(self.do('doctor')['issues'],[])

    def test_withdrawn_task_never_becomes_active_subject_teaching(self):
        self.configure_scopes()
        self.do('lesson.create',{'id':'withdrawn','knowledge_id':'k1','goal':'不执行','strategy':'WITHDRAWN_PRIVATE_STRATEGY',
                'acceptance':'不执行','next_task':'不执行','arrangement_status':'WITHDRAWN_BY_USER'})
        text=(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        self.assertNotIn('WITHDRAWN_PRIVATE_STRATEGY',text)
        self.assertIn('尚无有效教学任务',text)

    def test_pending_reviews_do_not_leak_to_other_subject(self):
        self.configure_scopes()
        review=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'合成测试'})
        execute(self.store,'handoff.ensure',{'target':'both'})
        own=(self.root/'subjects/linear/CODEX_HANDOFF.md').read_text(encoding='utf-8')
        other=(self.root/'subjects/english/CODEX_HANDOFF.md').read_text(encoding='utf-8')
        # Use the due day to make the scope filter observable.
        self.do('handoff.ensure',{'target':'both'},at=review['task']['due_date'])
        own=(self.root/'subjects/linear/CODEX_HANDOFF.md').read_text(encoding='utf-8')
        other=(self.root/'subjects/english/CODEX_HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn(review['task']['id'],own)
        self.assertNotIn(review['task']['id'],other)

    def test_scoped_write_failure_is_repaired_after_commit(self):
        self.configure_scopes()
        from studyos.handoff import atomic
        def fail(path,data):
            if path==self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md':raise OSError('synthetic failure')
            return atomic(path,data)
        with patch('studyos.handoff.atomic',fail):
            result=execute(self.store,'lesson.create',{'request_id':'scoped-failure','id':'new-task','knowledge_id':'k1',
                'goal':'目标','strategy':'策略','acceptance':'标准','next_task':'等待用户'})
        self.assertTrue(result['committed'])
        self.assertEqual(result['warnings'][0]['code'],'HANDOFF_FAILED')
        self.assertTrue(execute(self.store,'handoff.ensure',{'target':'both'})['regenerated'])
        self.assertEqual(self.count('lesson_tasks'),1)

    def test_invalid_scope_path_rejected_before_commit(self):
        cfg=self.store.config('materials');cfg['handoff_scopes']=[{'id':'bad','title':'bad','subject':'math2','directory':'../outside'}]
        with self.assertRaises(Error):self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'test','config':cfg})

    def test_filtered_navigation_uses_only_standard_files(self):
        self.materials()
        cfg=self.store.config('materials')
        cfg['subjects']['math2']['files']['outline']['role']='outline'
        cfg['subjects']['math2']['files']['other-outline']={'path':'outline.md','role':'outline'}
        cfg['handoff_scopes']=[{'id':'numbered','title':'编号交接','subject':'math2','prefix':'LA-TEST-01',
            'material_keys':['outline'],'directory':'subjects/numbered'}]
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'合成科目文件','config':cfg})
        p=self.root/'subjects/numbered/CODEX_HANDOFF.md'
        text=p.read_text(encoding='utf-8')
        self.assertIn('"知识条目总数":1',text)
        self.assertIn('LA-TEST-01',text)
        self.assertNotIn('LA-TEST-02',text)
        navigation=json.loads(text.split('## 科目资料导航\n\n')[1].split('\n\n')[0])
        self.assertEqual(len(navigation),1)
        result=execute(self.store,'handoff.ensure',{'target':'both'})
        self.assertFalse(result['regenerated'])

    def test_numbered_duplicates_and_overlapping_scopes_are_rejected(self):
        self.configure_scopes()
        cfg=self.store.config('materials')
        cfg['handoff_scopes'][0]['files']={'codex':'05_CODEX_HANDOFF.md','gpt':'06_GPT_TEACHING_HANDOFF.md'}
        with self.assertRaises(Error):
            self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'拒绝编号副本','config':cfg})
        cfg=self.store.config('materials')
        cfg['handoff_scopes'].append({'id':'duplicate','title':'重复范围','subject':'math2','directory':'subjects/duplicate'})
        with self.assertRaises(Error):
            self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'拒绝重复范围','config':cfg})

    def test_only_subject_files_are_written_and_show_routes_to_subject(self):
        self.selected_lesson_fixture()
        self.do('lesson.prepare',{'task_id':'first'})
        before=self.count('audit_events')
        result=execute(self.store,'handoff.export',{'target':'both'})
        self.assertEqual(len(result['files']),4)
        self.assertFalse((self.root/'CODEX_HANDOFF.md').exists())
        self.assertFalse((self.root/'GPT_TEACHING_HANDOFF.md').exists())
        self.assertFalse(any((self.root/'records/handoffs').rglob('*.md')))
        shown=execute(self.store,'handoff.show',{'target':'codex'})
        self.assertEqual(shown['scope_id'],'linear')
        self.assertEqual(shown['path'],str(self.root/'subjects/linear/CODEX_HANDOFF.md'))
        self.assertTrue(shown['fresh'])
        self.assertEqual(self.count('audit_events'),before)
        with self.assertRaises(Error):execute(self.store,'handoff.show',{'subject':'cs408'})

    def test_old_backup_layout_is_rendered_without_duplicate_files_or_config_change(self):
        self.configure_scopes()
        cfg=self.store.config('materials')
        cfg['handoff_scopes']+=[
            {'id':'english-numbered','title':'旧英语编号','subject':'english1','directory':'subjects/english/library',
             'files':{'codex':'05_CODEX_HANDOFF.md','gpt':'06_GPT_TEACHING_HANDOFF.md'}},
            {'id':'cs408','title':'旧408总览','subject':'cs408','directory':'subjects/408'},
            *[{'id':p,'title':p,'subject':'cs408','directory':'subjects/408/'+p,'prefix':p+'-'} for p in ('DS','CO','OS','NW')]]
        before=json.dumps(cfg,sort_keys=True)
        scopes=effective_scopes(self.store,cfg)
        self.assertEqual({s['id'] for s in scopes},{'linear','english','DS','CO','OS','NW'})
        self.assertTrue(all(s['files']=={'codex':'CODEX_HANDOFF.md','gpt':'GPT_TEACHING_HANDOFF.md'} for s in scopes))
        self.assertEqual(json.dumps(cfg,sort_keys=True),before)

    def test_no_configured_subjects_does_not_create_root_handoffs(self):
        cfg=self.store.config('materials');cfg['handoff_scopes']=[]
        self.do('materials.update',{'expected_revision':cfg['revision'],'reason':'合成空范围','config':cfg})
        result=execute(self.store,'handoff.ensure',{'target':'both'})
        self.assertEqual(result['files'],[])
        self.assertFalse((self.root/'CODEX_HANDOFF.md').exists())
        self.assertFalse((self.root/'GPT_TEACHING_HANDOFF.md').exists())

    def test_prepare_selects_first_lesson_and_later_creation_does_not_jump(self):
        self.selected_lesson_fixture()
        self.assertEqual(self.scoped_task_id(),'second')
        self.do('lesson.prepare',{'task_id':'first'})
        self.assertEqual(self.scoped_task_id(),'first')
        self.do('lesson.create',{'id':'third','knowledge_id':'k2','goal':'后续任务',
            'strategy':'不能覆盖已选课程','acceptance':'标准','next_task':'等待'})
        self.assertEqual(self.scoped_task_id(),'first')
        own=(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        self.assertIn('first-strategy',own)
        self.assertNotIn('不能覆盖已选课程',own)
        self.assertIn(str(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md'),own)
        self.assertIn('本次Markdown课件（需上传）',own)
        self.assertNotIn('根目录兼容快照',own)
        self.assertEqual(self.count('learning_sessions'),0)
        self.do('lesson.prepare',{'task_id':'second'})
        self.assertEqual(self.scoped_task_id(),'second')
        other=(self.root/'subjects/english/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        self.assertNotIn('second-strategy',other)

    def test_paused_session_precedes_a_different_prepared_task(self):
        self.selected_lesson_fixture()
        session=self.do('lesson.start',{'task_id':'first'})
        self.do('lesson.pause',{'session_id':session['session_id'],'expected_revision':1,'checkpoint':'真实合成断点'})
        self.do('lesson.prepare',{'task_id':'second'})
        self.assertEqual(self.scoped_task_id(),'first')

    def test_selected_withdrawn_proposal_falls_back_to_valid_task(self):
        self.selected_lesson_fixture()
        self.do('lesson.prepare',{'task_id':'first'})
        self.do('lesson.create',{'id':'first','knowledge_id':'k1','expected_revision':1,
            'goal':'已撤销','strategy':'撤销不应被选中','acceptance':'标准','next_task':'等待',
            'arrangement_status':'WITHDRAWN_BY_USER'})
        self.assertEqual(self.scoped_task_id(),'second')
