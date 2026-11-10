from support import Case
from studyos.common import Error
from studyos.materials import text_ranges
from studyos.handoff import bodies, context


class CoursewareTests(Case):
    def test_list_uses_source_sequence_rather_than_uuid(self):
        self.do('knowledge.import',{'catalog':{'id':'ordered','subject':'english1','name':'合成课件顺序'},
            'points':[{'id':'z-first','external_id':'FIRST','name':'先学','criteria':'解释','source_order':0},
                      {'id':'a-second','external_id':'SECOND','name':'后学','criteria':'应用','source_order':1}]})
        result=self.do('knowledge.list',{'subject':'english1'})
        self.assertEqual([x['external_id'] for x in result],['FIRST','SECOND'])

    def test_disjoint_source_rows_without_loading_intervening_material(self):
        self.materials()
        self.do('materials.register',{'id':'outline','expected_revision':1,'subject':'math2','path_key':'outline','role':'outline',
            'bindings':[{'knowledge_id':'k1','locator':{'line_ranges':[[1,1],[3,3]]}}]})
        result=self.do('materials.read',{'knowledge_id':'k1','level':2,'reason':'精确检查不相邻来源行'})
        excerpt=result['materials'][0]['text']
        self.assertIn('# 大纲',excerpt)
        self.assertIn('目标：解释并应用',excerpt)
        self.assertNotIn('LA-TEST-01',excerpt)
        for invalid in ([[0,1]],[[1,3],[2,3]],[[1,4]],[[True,2]],[]):
            with self.assertRaises(Error):text_ranges({'line_ranges':invalid},3)

    def test_navigation_containers_reject_teaching_and_assessment(self):
        self.do('knowledge.import',{'catalog':{'id':'linear','subject':'math2','name':'合成教材（仅测试）'},
            'points':[{'id':'nav','external_id':'NAV','name':'目录','criteria':'只导航','navigation_only':True}]})
        for op,data in [('lesson.create',{'knowledge_id':'nav'}),('assessment.create',{'knowledge_id':'nav'})]:
            with self.assertRaises(Error) as caught:self.do(op,data)
            self.assertEqual(caught.exception.code,'NAVIGATION_ONLY')
        self.assertEqual(self.count('lesson_tasks'),0)
        self.assertEqual(self.count('assessments'),0)

    def test_guidance_is_rendered_from_registered_configuration(self):
        cfg=self.store.config('materials');cfg['curriculum_guidance']='依据课件；等待真实开始'
        self.do('materials.update',{'config':cfg,'expected_revision':1,'reason':'合成课件执行规则'})
        with self.store.connect(True) as c:
            meta,task,k,config=context(self.store,c)
            codex,gpt=bodies(self.store,c,meta,task,k,config)
        self.assertIn(cfg['curriculum_guidance'],codex)
        self.assertIn(cfg['curriculum_guidance'],gpt)
        self.assertEqual(self.count('learning_sessions'),0)
