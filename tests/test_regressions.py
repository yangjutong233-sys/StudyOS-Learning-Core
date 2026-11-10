from pathlib import Path
from unittest.mock import patch
import copy
import json
import sqlite3
from studyos.app import execute
from studyos.common import Error, canonical, sha, uid
from studyos.handoff import FILES
from studyos.maintenance import create_backup,restore_backup
from studyos.storage import Store
from tests.support import Case


class RegressionTests(Case):
    def test_configuration_can_change_review_intervals_without_rewriting_history(self):
        first=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'原规则'})
        cfg=self.store.config('review_policy');cfg['intervals']=[2,5,10]
        self.do('policy.update',{'name':'review_policy','config':cfg,'expected_revision':1,'reason':'用户实际表现调整'})
        new=self.do('review.create',{'kind':'knowledge','knowledge_id':'k2','reason':'采用新版规则'})
        self.assertEqual(first['task']['suggested_date'],'2026-01-02')
        self.assertEqual(new['task']['suggested_date'],'2026-01-03')
        a,_=self.assessment(at='2026-01-02')
        r=self.do('review.complete',{'id':first['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-02')
        self.assertEqual(r['next']['suggested_date'],'2026-01-05')

    def test_sessions_resume_without_losing_checkpoint_or_minutes(self):
        session=self.lesson()
        self.do('lesson.pause',{'session_id':session['session_id'],'expected_revision':1,'checkpoint':'暂停在步骤二',
                  'minutes':5,'time_basis':'用户确认','covered':['步骤一']})
        resumed=self.do('lesson.start',{'task_id':'t1'})
        self.assertTrue(resumed['resumed']);self.assertEqual(session['session_id'],resumed['session_id'])
        self.do('lesson.finish',{'session_id':resumed['session_id'],'expected_revision':3,'checkpoint':'完成教学','minutes':7,
                              'time_basis':'用户确认','covered':['步骤二']})
        self.assertEqual(self.count('learning_sessions'),1)
        with self.store.connect(True) as c: self.assertEqual(c.execute('SELECT SUM(minutes) FROM session_records').fetchone()[0],12)

    def test_dual_file_interruption_repaired_from_same_snapshot(self):
        from studyos.handoff import atomic as original
        def partial(path,data):
            if Path(path)==self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md': raise OSError('synthetic interruption')
            return original(path,data)
        with patch('studyos.handoff.atomic',partial):
            with self.assertRaises(OSError): execute(self.store,'handoff.export',{'target':'both'})
        result=execute(self.store,'handoff.ensure',{'target':'both'});self.assertTrue(result['regenerated'])
        headers=[]
        for name in FILES.values(): headers.append(json.loads((self.root/'subjects/linear'/name).read_text(encoding='utf-8').splitlines()[0][13:-4]))
        self.assertEqual(headers[0]['snapshot_id'],headers[1]['snapshot_id'])

    def test_stale_database_identity_and_renderer_are_detected(self):
        p=self.root/'subjects/linear/CODEX_HANDOFF.md';header,body=p.read_text(encoding='utf-8').split('\n',1)
        metadata=json.loads(header[13:-4]);metadata['database_id']='other database';metadata['renderer_version']=0
        p.write_text('<!-- studyos '+canonical(metadata)+' -->\n'+body,encoding='utf-8')
        self.assertTrue(execute(self.store,'handoff.ensure')['regenerated'])

    def test_material_version_is_locked_to_task_and_original_answer_is_db_immutable(self):
        self.lesson()
        p=self.root/'synthetic_materials/outline.md';p.write_text('# 新大纲\n新条件\n新题\n',encoding='utf-8')
        self.do('materials.register',{'id':'outline','expected_revision':1,'subject':'math2','path_key':'outline','role':'outline',
             'bindings':[{'knowledge_id':'k1','locator':{'line_start':1,'line_end':3}}]})
        with self.assertRaises(Error): self.do('lesson.prepare',{'task_id':'t1'})
        _,attempt=self.assessment()
        with self.store.connect() as c:
            with self.assertRaises(sqlite3.IntegrityError): c.execute('UPDATE attempts SET answer=? WHERE id=?',('伪造',attempt['id']))

    def test_same_text_new_id_does_not_hide_answer_exposure(self):
        b={'knowledge_id':'k1','question':'同一原题','answer':'实际答案','process':'过程','form':'recall','dimensions':['concept'],
           'source':'codex_independent','confirmed':True,'occurred_at':self.store.at,'evaluation':{'result':'PASS','basis':'依据','reference':'标准'}}
        self.do('attempt.add',{**b,'assistance':'ANSWER_SEEN','question_id':'q-before'})
        test=self.do('assessment.create',{'knowledge_id':'k1','blueprint':{'criteria':'提取','rationale':'验证提示暴露',
                         'min_attempts':1,'forms':['recall'],'dimensions':['concept']}})
        self.do('attempt.add',{**b,'assistance':'NONE','question_id':'q-after','assessment_id':test['id']})
        result=self.do('assessment.submit',{'id':test['id'],'expected_revision':1})
        self.assertEqual(result['result'],'HINTED')

    def test_sunday_shift_postpone_preserves_original_date(self):
        self.confirm()
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'周日到期','due_date':'2026-01-04'})
        self.assertEqual(s['task']['suggested_date'],'2026-01-04');self.assertEqual(s['task']['due_date'],'2026-01-05')
        self.do('review.reschedule',{'id':s['task']['id'],'expected_revision':1,'due_date':'2026-01-08','reason':'用户临时工作'})
        history=execute(self.store,'review.history',{'id':s['id']})
        self.assertEqual(history[0]['suggested_date'],'2026-01-04');self.assertEqual(history[0]['due_date'],'2026-01-08')

    def test_complete_backup_contains_external_material_and_offline_rebind_manifest(self):
        self.materials();result=create_backup(self.store,'complete')
        destination=self.root.parent/'portable-restored'
        restored=restore_backup(result['path'],destination,self.root)
        self.assertTrue(restored['material_paths'])
        entry=restored['material_paths'][0]
        self.assertEqual(sha(destination/entry['backup_path']),entry['sha256'])

    def test_comprehensive_bad_targets_and_invalid_dates_do_not_commit(self):
        before=self.count('review_series')
        for data in [
            {'kind':'knowledge','knowledge_id':'missing','reason':'无效'},
            {'kind':'knowledge','knowledge_id':'k1','reason':'无效日期','due_date':'2026-02-30'},
            {'kind':'comprehensive','members':['k1','missing'],'reason':'无效成员'},
        ]:
            with self.assertRaises(Error): self.do('review.create',data)
        self.assertEqual(before,self.count('review_series'))

    def test_audit_entity_revision_and_no_business_changes_on_ensure(self):
        session=self.lesson()
        self.do('lesson.finish',{'session_id':session['session_id'],'expected_revision':1,'checkpoint':'完成','covered':['讲解']})
        events=execute(self.store,'audit.list',{'entity_type':'session','id':session['session_id']})
        self.assertEqual([e['new_revision'] for e in events],[1,2]);self.assertIsNone(events[0]['previous_revision'])
        before={t:self.count(t) for t in ('audit_events','session_records','mastery_assessments','review_tasks','daily_plans')}
        execute(self.store,'handoff.export',{'target':'both'})
        self.assertEqual(before,{t:self.count(t) for t in before})

    def test_day_exception_can_be_revised_without_overwriting_week(self):
        self.confirm();week=self.store.config('schedule')
        first=self.do('schedule.exception.add',{'day':'2026-01-02','authorization_ref':'一次调整',
                 'config':{'windows':[['08:00','10:00']],'blocked':[],'max_minutes':120,'rest':False}})
        changed=self.do('schedule.exception.add',{'day':'2026-01-02','expected_revision':1,'authorization_ref':'再次明确调整',
                 'config':{'windows':[['08:00','09:00']],'blocked':[],'max_minutes':60,'rest':False}})
        self.assertEqual(first['id'],changed['id']);self.assertEqual(changed['revision'],2)
        self.assertEqual(week,self.store.config('schedule'));self.assertEqual(self.count('schedule_exceptions'),1)
        events=execute(self.store,'audit.list',{'entity_type':'schedule_exception','id':first['id']})
        self.assertEqual(len(events),2)

    def test_duplicate_daily_plan_id_cannot_bypass_capacity(self):
        self.confirm()
        context=execute(self.store,'planning-context',{'day':'2026-01-02'})
        raw={'id':'p1','day':'2026-01-02','schedule_hash':context['capacity']['schedule_hash'],'items':[
          {'id':'i1','knowledge_id':'k1','start':'08:00','end':'09:00','goal':'g','reason':'r','acceptance':'a'}]}
        self.do('plan.apply',raw)
        with self.assertRaises(Error): self.do('plan.apply',{**raw,'id':'p2'})
        self.do('plan.record-progress',{'plan_id':'p1','id':'i1','expected_revision':1,'status':'COMPLETED','reason':'实际完成'})
        wrong=copy.deepcopy(raw);wrong['expected_revision']=1;wrong['items'][0]['goal']='篡改已完成目标'
        with self.assertRaises(Error): self.do('plan.revise',wrong)

    def test_repeated_question_cannot_satisfy_multiple_question_blueprint(self):
        a=self.do('assessment.create',{'knowledge_id':'k1','blueprint':{'criteria':'大纲','rationale':'需要两道独立题',
             'min_attempts':2,'forms':['recall','application'],'dimensions':['concept']}})
        for form in ('recall','application'):
            self.do('attempt.add',{'knowledge_id':'k1','assessment_id':a['id'],'question_id':'same-q','question':'同一道原题',
             'answer':'原答','process':'过程','form':form,'dimensions':['concept'],'source':'codex_independent','confirmed':True,
             'assistance':'NONE','evaluation':{'result':'PASS','basis':'依据','reference':'标准'}})
        with self.assertRaises(Error): self.do('assessment.submit',{'id':a['id'],'expected_revision':1})

    def test_legacy_root_is_protected_before_any_write(self):
        legacy=self.root.parent/'legacy';legacy.mkdir();(legacy/'study_data').mkdir()
        before=list(legacy.iterdir())
        with self.assertRaises(Error): execute(Store(legacy),'init')
        with self.assertRaises(Error): execute(Store(legacy),'handoff.ensure')
        self.assertEqual(before,list(legacy.iterdir()))

    def test_initialization_cannot_silently_confirm_schedule(self):
        root=self.root.parent/'unconfirmed';(root/'config').mkdir(parents=True)
        config=self.store.config('schedule');config['status']='confirmed'
        (root/'config/schedule.json').write_text(canonical(config),encoding='utf-8')
        with self.assertRaises(Error): execute(Store(root),'init')
        self.assertFalse((root/'database/studyos.sqlite3').exists())

    def test_next_day_ensure_refreshes_due_snapshot_without_new_event(self):
        before=self.count('audit_events')
        result=execute(Store(self.root,'2026-01-02'),'handoff.ensure',{'target':'both'})
        self.assertTrue(result['regenerated']);self.assertEqual(result['metadata']['business_date'],'2026-01-02')
        self.assertEqual(before,self.count('audit_events'))

    def test_subject_directories_read_only_the_requested_branch(self):
        self.materials()
        self.do('knowledge.import',{'catalog':{'id':'english-c','subject':'english1','name':'独立英语目录'},
           'points':[{'id':'english-k','external_id':'EN1','name':'词义','criteria':'辨析'}]})
        folder=self.root/'other-subject';folder.mkdir();other=folder/'english.md';other.write_text('# English\nmeaning\n',encoding='utf-8')
        cfg=self.store.config('materials');cfg['subjects']['english1']={'path':str(folder),'files':{'outline':{'path':'english.md'}}}
        self.do('materials.update',{'config':cfg,'expected_revision':2,'reason':'英语独立目录'})
        self.do('materials.register',{'id':'english-outline','subject':'english1','path_key':'outline','role':'outline',
             'bindings':[{'knowledge_id':'english-k','locator':{'line_start':1,'line_end':2}}]})
        original=Path.read_text;opened=[]
        def tracked(path,*args,**kwargs):
            opened.append(path.resolve());return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',tracked):
            value=execute(self.store,'materials.read',{'knowledge_id':'k1','level':2,'reason':'当前数学目标'})
        self.assertIn('目标',value['materials'][0]['text']);self.assertNotIn(other.resolve(),opened)

    def test_original_attempt_time_is_not_replaced_by_late_import_time(self):
        result=self.do('attempt.add',{'knowledge_id':'k1','question':'延迟归档题','answer':'原答','process':'过程','form':'recall',
             'source':'gpt_teaching','confirmed':True,'assistance':'UNKNOWN','occurred_at':'2026-01-01T10:15:00+08:00',
             'evaluation':{'result':'UNDETERMINED','basis':'待核验','reference':'标准'}},at='2026-01-03')
        a=execute(self.store,'attempt.list')[0]
        self.assertEqual(a['occurred_at'],'2026-01-01T10:15:00+08:00')
        event=execute(self.store,'audit.list',{'id':result['id']})[0]
        self.assertEqual(event['occurred_at'],a['occurred_at'])
