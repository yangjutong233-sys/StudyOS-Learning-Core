"""Plan lifecycle integration tests. Synthetic data in temporary roots only."""
import copy
import json
import subprocess
import sys
from studyos.app import execute
from studyos.common import Error
from tests.support import Case


class FinalAuditFixTests(Case):
    def setUp(self):
        super().setUp()
        self.confirm(all_days=True)

    def plan(self, pid='p', iid='i', day='2026-01-03', review_id=None, start='08:00', end='09:00'):
        ctx=execute(self.store,'planning-context',{'day':day})
        item={'id':iid,'knowledge_id':'k1','start':start,'end':end,'goal':'g','reason':'r','acceptance':'a'}
        if review_id:item['review_id']=review_id
        return {'id':pid,'day':day,'schedule_hash':ctx['capacity']['schedule_hash'],'items':[item]}

    def waiting_review(self):
        s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'待补证据'})
        a,_=self.assessment('UNDETERMINED',at='2026-01-02')
        self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-02')
        return s['task']['id']

    def progress(self,p,status,revision=0,at='2026-01-03T09:00:00+08:00',plan_version=1):
        return self.do('plan.record-progress',{'plan_id':p['id'],'id':p['items'][0]['id'],
            'expected_revision':plan_version,'expected_item_revision':revision,'status':status,'reason':'合成状态变更'},at=at)

    def test_F01_reactivation_cannot_duplicate_moved_review(self):
        rid=self.waiting_review();old=self.plan(review_id=rid)
        self.do('plan.apply',old,at='2026-01-02')
        self.progress(old,'DEFERRED')
        new=self.plan('next','next-item','2026-01-04',rid)
        self.do('plan.apply',new,at='2026-01-03')
        before=self.count('audit_events')
        with self.assertRaises(Error) as blocked:self.progress(old,'ACTIVE',1)
        self.assertEqual(blocked.exception.code,'DUPLICATE_REVIEW')
        self.assertEqual(self.count('audit_events'),before)
        self.assertEqual(execute(self.store,'plan.show',{'id':old['id']})['items'][0]['status'],'DEFERRED')
        self.assertEqual(execute(self.store,'plan.show',{'id':new['id']})['items'][0]['status'],'PENDING')
        with self.assertRaises(Error) as blocked:self.progress(old,'COMPLETED',1)
        self.assertEqual(blocked.exception.code,'DUPLICATE_REVIEW')
        self.progress(new,'DEFERRED')
        self.assertEqual(self.progress(old,'ACTIVE',1)['status'],'ACTIVE')

    def test_F02_completed_work_survives_new_window_and_remaining_work_can_move(self):
        p=self.plan(day='2026-01-10');self.do('plan.apply',p,at='2026-01-10T07:00:00+08:00')
        self.progress(p,'COMPLETED',at='2026-01-10T09:00:00+08:00')
        cfg=self.store.config('schedule')
        for spec in cfg['week'].values():spec.update(windows=[['09:00','12:00']],max_minutes=180)
        self.do('schedule.update',{'config':cfg,'expected_revision':3,'authorization_ref':'合成改时间'},at='2026-01-10T09:02:00+08:00')
        revised=self.plan(day='2026-01-10');revised['expected_revision']=1
        revised['items'].append({**p['items'][0],'id':'remaining','start':'09:00','end':'10:00'})
        self.assertTrue(execute(self.store,'plan.validate',revised)['valid'])
        self.assertEqual(self.do('plan.revise',revised,at='2026-01-10T09:04:00+08:00')['version'],2)
        items=execute(self.store,'plan.show',{'id':p['id']})['items']
        done=next(x for x in items if x['id']=='i')
        self.assertEqual((done['status'],done['start'],done['end']),('COMPLETED','08:00','09:00'))

    def completed_plan(self):
        p=self.plan(day='2026-01-10')
        self.do('plan.apply',p,at='2026-01-10T07:00:00+08:00')
        self.progress(p,'COMPLETED',at='2026-01-10T09:00:00+08:00')
        return p

    def change_day(self,p,**spec):
        self.do('schedule.exception.add',{'day':p['day'],'authorization_ref':'合成日期例外',
            'config':{'windows':[['09:00','12:00']],'blocked':[],'max_minutes':180,'rest':False,**spec}},at='2026-01-10T09:02:00+08:00')
        revised=copy.deepcopy(p);revised['expected_revision']=1
        revised['schedule_hash']=execute(self.store,'planning-context',{'day':p['day']})['capacity']['schedule_hash']
        return revised

    def test_completed_fields_remain_immutable_after_window_change(self):
        p=self.completed_plan();r=self.change_day(p)
        for changed in ({'start':'09:00','end':'10:00'},{'goal':'rewrite'},{'review_id':'invented'}):
            bad=copy.deepcopy(r);bad['items'][0].update(changed)
            with self.subTest(changed=changed),self.assertRaises(Error):self.do('plan.revise',bad)
        with self.assertRaises(Error):self.do('plan.revise',{**r,'items':[]})
        self.assertEqual(execute(self.store,'plan.show',{'id':p['id']})['version'],1)

    def test_reduced_daily_limit_reserves_completed_minutes(self):
        p=self.completed_plan();r=self.change_day(p,max_minutes=90)
        r['items'].append({**p['items'][0],'id':'remaining','start':'09:00','end':'09:30'})
        self.assertTrue(execute(self.store,'plan.validate',r)['valid'])
        bad=copy.deepcopy(r);bad['items'][1]['end']='09:31'
        with self.assertRaises(Error) as rejected:execute(self.store,'plan.validate',bad)
        self.assertEqual(rejected.exception.code,'SCHEDULE_CONFLICT')
        self.assertEqual(self.do('plan.revise',r)['version'],2)

    def test_limit_below_completed_work_preserves_history_without_new_capacity(self):
        p=self.completed_plan();r=self.change_day(p,max_minutes=30)
        self.assertTrue(execute(self.store,'plan.validate',r)['valid'])
        bad=copy.deepcopy(r);bad['items'].append({**p['items'][0],'id':'remaining','start':'09:00','end':'09:01'})
        with self.assertRaises(Error):execute(self.store,'plan.validate',bad)
        self.assertEqual(self.do('plan.revise',r)['version'],2)

    def test_rest_exception_preserves_completed_history_but_no_new_work(self):
        p=self.completed_plan();r=self.change_day(p,rest=True,windows=[],max_minutes=0)
        self.assertTrue(execute(self.store,'plan.validate',r)['valid'])
        bad=copy.deepcopy(r);bad['items'].append({**p['items'][0],'id':'remaining','start':'09:00','end':'09:30'})
        with self.assertRaises(Error):execute(self.store,'plan.validate',bad)
        self.assertEqual(self.do('plan.revise',r)['version'],2)

    def test_completed_history_still_blocks_overlapping_work(self):
        p=self.completed_plan();r=self.change_day(p,windows=[['08:00','12:00']])
        r['items'].append({**p['items'][0],'id':'overlap','start':'08:30','end':'09:30'})
        with self.assertRaises(Error):execute(self.store,'plan.validate',r)

    def test_caller_cannot_inject_completed_status_to_bypass_window(self):
        p=self.plan(start='13:00',end='14:00');p['items'][0]['status']='COMPLETED'
        with self.assertRaises(Error):self.do('plan.apply',p)
        self.assertEqual(self.count('daily_plans'),0)

    def test_deferred_slot_is_free_but_reactivation_checks_conflict(self):
        p=self.plan();self.do('plan.apply',p);self.progress(p,'DEFERRED')
        r=copy.deepcopy(p);r['expected_revision']=1
        r['items'].append({**p['items'][0],'id':'replacement'})
        self.do('plan.revise',r)
        with self.assertRaises(Error) as rejected:self.progress(p,'ACTIVE',1,plan_version=2)
        self.assertEqual(rejected.exception.code,'SCHEDULE_CONFLICT')
        self.do('plan.revise',{**p,'expected_revision':2})
        self.assertEqual(self.progress(p,'ACTIVE',1,plan_version=3)['status'],'ACTIVE')

    def test_reactivation_checks_new_capacity_and_window(self):
        for mode in ('window','capacity'):
            with self.subTest(mode=mode):
                p=self.plan('p-'+mode,'i-'+mode,'2026-01-0'+('5' if mode=='window' else '6'))
                self.do('plan.apply',p);self.progress(p,'DEFERRED')
                r=self.change_day(p,windows=[['09:00','12:00']] if mode=='window' else [['08:00','12:00']],max_minutes=30 if mode=='capacity' else 180)
                self.do('plan.revise',r)
                with self.assertRaises(Error):self.progress(p,'ACTIVE',1,plan_version=2)

    def test_revise_cannot_acquire_review_owned_by_another_plan(self):
        rid=self.waiting_review();p=self.plan(review_id=rid);self.do('plan.apply',p)
        q=self.plan('q','j','2026-01-04',rid);self.do('plan.apply',{**q,'items':[]})
        with self.assertRaises(Error) as rejected:self.do('plan.revise',{**q,'expected_revision':1})
        self.assertEqual(rejected.exception.code,'DUPLICATE_REVIEW')

    def test_ended_review_cannot_be_reactivated_but_can_record_completion(self):
        for outcome in ('COMPLETED','CANCELLED'):
            with self.subTest(outcome=outcome):
                s=self.do('review.create',{'kind':'knowledge','knowledge_id':'k1','reason':'结束边界'})
                p=self.plan('p-'+outcome,'i-'+outcome,'2026-01-03' if outcome=='COMPLETED' else '2026-01-04',s['task']['id'])
                self.do('plan.apply',p)
                if outcome=='COMPLETED':
                    a,_=self.assessment(at='2026-01-02')
                    self.do('review.complete',{'id':s['task']['id'],'expected_revision':1,'assessment_id':a['id']},at='2026-01-02')
                else:self.do('review.cancel',{'id':s['task']['id'],'expected_revision':1,'reason':'取消'})
                with self.assertRaises(Error):self.progress(p,'ACTIVE')
                if outcome=='COMPLETED':self.assertEqual(self.progress(p,'COMPLETED')['status'],'COMPLETED')
                else:
                    with self.assertRaises(Error):self.progress(p,'COMPLETED')
                    self.assertEqual(self.progress(p,'DEFERRED')['status'],'DEFERRED')

    def test_reactivation_is_idempotent_and_stale_revisions_do_not_write(self):
        p=self.plan();self.do('plan.apply',p);self.progress(p,'DEFERRED')
        raw={'request_id':'resume-once','plan_id':p['id'],'id':p['items'][0]['id'],
            'expected_revision':1,'expected_item_revision':1,'status':'ACTIVE','reason':'恢复'}
        execute(self.store,'plan.record-progress',raw);before=self.count('audit_events')
        self.assertTrue(execute(self.store,'plan.record-progress',raw)['duplicate'])
        with self.assertRaises(Error):self.progress(p,'ACTIVE',1)
        self.assertEqual(self.count('audit_events'),before)

    def test_two_processes_cannot_activate_same_review_twice(self):
        rid=self.waiting_review();p=self.plan(review_id=rid);self.do('plan.apply',p);self.progress(p,'DEFERRED')
        q=self.plan('q','j','2026-01-04',rid);self.do('plan.apply',q);self.progress(q,'DEFERRED')
        processes=[]
        for i,plan in enumerate((p,q)):
            path=self.root/f'activate-{i}.json'
            path.write_text(json.dumps({'request_id':f'activate-{i}','plan_id':plan['id'],'id':plan['items'][0]['id'],
                'expected_revision':1,'expected_item_revision':1,'status':'ACTIVE','reason':'并发恢复'}),encoding='utf-8')
            processes.append(subprocess.Popen([sys.executable,'-m','studyos','plan','record-progress','--root',str(self.root),
                '--at','2026-01-03','--json','--input',str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE))
        results=[]
        for process in processes:
            output,error=process.communicate(timeout=25);results.append((process.returncode,json.loads(output)))
        self.assertEqual(sorted(code for code,_ in results),[0,2])
        self.assertEqual(next(r['error']['code'] for code,r in results if code==2),'DUPLICATE_REVIEW')
        self.assertTrue(execute(self.store,'doctor')['ok'])

    def test_doctor_detects_preexisting_duplicate_bindings_without_repair(self):
        rid=self.waiting_review();p=self.plan(review_id=rid);self.do('plan.apply',p);self.progress(p,'DEFERRED')
        q=self.plan('q','j','2026-01-04',rid);self.do('plan.apply',q)
        # Simulate a database affected by the old bug, in this temporary root only.
        with self.store.connect() as c:c.execute("UPDATE plan_items SET status='ACTIVE' WHERE plan_id=?",(p['id'],))
        report=execute(self.store,'doctor')
        self.assertFalse(report['ok'])
        self.assertIn('DUPLICATE_REVIEW_BINDING',[i['code'] for i in report['issues']])
        self.assertEqual(execute(self.store,'plan.show',{'id':p['id']})['items'][0]['status'],'ACTIVE')
