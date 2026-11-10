from pathlib import Path
from unittest.mock import patch
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from studyos.app import execute
from studyos.common import Error, canonical, digest, sha, uid
from studyos.maintenance import create_backup, restore_backup, verify_backup
from studyos.reports import markdown_template
from studyos.storage import Store
from tests.support import Case


class ReportTests(Case):
    def report(self,version=1):
        s=self.lesson()
        return {'report_id':'r1','version':version,'task_id':'t1','task_version':1,'session_id':s['session_id'],
             'covered':['讲解定义与条件'],'checkpoint':'待独立考核','minutes':20,'time_basis':'用户确认',
             'unfinished_training':['新的变式'], 'attempts':[{'id':'report-attempt-1','question':'教学练习题','answer':'原始作答',
                'process':'尝试过程','form':'application','dimensions':['concept'],'assistance':'LIGHT_HINT','answer_seen':False,
                'evaluation':{'result':'FAIL','basis':'教学评分依据','reference':'标准思路','error_reason':'概念条件遗漏','error_type':'CONCEPT'}}]}

    def write_pair(self,data,md=None):
        mp=self.root/f'report-{data["version"]}.md';jp=self.root/f'report-{data["version"]}.json'
        mp.write_text(markdown_template(data) if md is None else md,encoding='utf-8')
        jp.write_text(canonical(data),encoding='utf-8')
        return {'markdown_path':str(mp),'json_path':str(jp),'semantic_review_ref':'Codex实际逐项核验（合成测试）'}

    def test_K_inconsistent_pair_pending_without_mastery_then_new_revision(self):
        data=self.report();md=markdown_template(data).replace('原始作答','被改写答案')
        result=self.do('report.import',self.write_pair(data,md))
        self.assertEqual(result['status'],'PENDING');self.assertIn('report-attempt-1.answer',result['differences'])
        self.assertEqual(self.count('attempts'),0);self.assertEqual(self.count('mastery_assessments'),0)
        data['version']=2
        result=self.do('report.resolve',{**self.write_pair(data),'reason':'保留真实原答，修正Markdown'})
        self.assertEqual(result['status'],'ACCEPTED');self.assertEqual(self.count('attempts'),1)
        self.assertEqual(self.count('mastery_assessments'),0)
        self.assertEqual(execute(self.store,'knowledge.show',{'id':'k1'})['status'],'ASSESSMENT_PENDING')
        self.assertIn('原始作答',(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8'))

    def test_duplicate_report_different_request_does_not_duplicate_attempts(self):
        data=self.report();pair=self.write_pair(data)
        self.do('report.import',pair);n=self.count('attempts')
        self.assertTrue(self.do('report.import',pair)['duplicate_report']);self.assertEqual(n,self.count('attempts'))

    def test_semantic_review_is_required_and_originals_preserved(self):
        data=self.report();pair=self.write_pair(data);pair.pop('semantic_review_ref')
        result=self.do('report.import',pair)
        self.assertEqual(result['status'],'PENDING')
        report=execute(self.store,'report.show',{'id':result['artifact_id']})
        self.assertIn('原始作答',report['text']);self.assertEqual(self.count('session_records'),0)

    def test_accepted_report_correction_preserves_raw_and_does_not_double_time(self):
        data=self.report();self.do('report.import',self.write_pair(data))
        self.assertEqual(execute(self.store,'summary')['learning_time']['confirmed_minutes'],20)
        data['version']=2;data['minutes']=15
        data['attempts'][0]['evaluation']['error_reason']='核验后更精确的原因'
        data['attempts'][0]['expected_evaluation_revision']=1
        result=self.do('report.resolve',{**self.write_pair(data),'reason':'纠正确认时长和错因，保留原答'})
        self.assertEqual(result['status'],'ACCEPTED')
        self.assertEqual(self.count('attempts'),1);self.assertEqual(self.count('session_records'),2)
        self.assertEqual(self.count('attempt_evaluations'),2)
        self.assertEqual(execute(self.store,'summary')['learning_time']['confirmed_minutes'],15)
        self.assertTrue(execute(self.store,'doctor')['ok'])

    def test_report_revision_cannot_rewrite_original_answer(self):
        data=self.report();self.do('report.import',self.write_pair(data))
        data['version']=2;data['attempts'][0]['answer']='伪造新答案'
        result=self.do('report.resolve',{**self.write_pair(data),'reason':'请求修订'})
        self.assertEqual(result['status'],'PENDING');self.assertIn('immutable_original:report-attempt-1',result['differences'])
        self.assertEqual(execute(self.store,'attempt.list')[0]['answer'],'原始作答')

    def test_report_after_session_finish_requires_explicit_batch_and_preserves_completion(self):
        data=self.report()
        finished=self.do('lesson.finish',{'session_id':data['session_id'],'expected_revision':1,'checkpoint':'教学完成',
                    'minutes':20,'time_basis':'用户确认','covered':['讲解']})
        result=self.do('report.import',self.write_pair(data));self.assertEqual(result['status'],'PENDING')
        data['version']=2
        result=self.do('report.resolve',{**self.write_pair(data),'reason':'归档对应已完成批次',
                                      'supersedes_record_id':finished['record_id']})
        self.assertEqual(result['status'],'ACCEPTED')
        self.assertEqual(execute(self.store,'summary')['learning_time']['confirmed_minutes'],20)
        self.assertEqual(execute(self.store,'lesson.show',{'id':'t1'})['sessions'][0]['status'],'COMPLETED')


class BackupTests(Case):
    def test_backup_waits_for_inflight_writer_then_captures_committed_state(self):
        code=r'''
from studyos.common import operation_lock
from studyos.storage import Store
import sys,time
s=Store(sys.argv[1])
with operation_lock(s.root):
 with s.connect() as c:
  c.execute('BEGIN IMMEDIATE')
  c.execute("INSERT INTO meta VALUES('concurrent_backup_fixture','committed')")
  print('LOCKED',flush=True)
  time.sleep(.4)
  c.commit()
'''
        process=subprocess.Popen([sys.executable,'-c',code,str(self.root)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assertEqual(process.stdout.readline().strip(),b'LOCKED')
        result=create_backup(self.store)
        stdout,stderr=process.communicate(timeout=10);self.assertEqual(process.returncode,0,stderr)
        c=sqlite3.connect(Path(result['path'])/'snapshot.sqlite3')
        try: self.assertEqual(c.execute("SELECT value FROM meta WHERE key='concurrent_backup_fixture'").fetchone()[0],'committed')
        finally: c.close()

    def test_G_backup_includes_uncheckpointed_wal_and_restores_files(self):
        self.lesson();self.assessment('FAIL')
        # Keep a read connection and disable autocheckpoint so committed WAL is meaningful.
        pin=self.store.connect();self.addCleanup(pin.close)
        pin.execute('PRAGMA wal_autocheckpoint=0');pin.execute('BEGIN');pin.execute('SELECT * FROM meta').fetchall()
        self.do('lesson.pause',{'session_id':execute(self.store,'summary')['selected_task'] and self._session(),
                 'expected_revision':1,'checkpoint':'真实断点','minutes':12,'time_basis':'用户确认','covered':['概念']})
        result=create_backup(self.store)
        self.assertTrue(result['restore_verified'])
        manifest=verify_backup(result['path'])
        self.assertEqual(manifest['counts']['session_records'],1)
        destination=self.root.parent/'restored'
        restored=restore_backup(result['path'],destination,self.root)
        self.assertEqual(restored['status'],'RESTORE_VERIFIED')
        self.assertTrue((destination/'subjects/linear/CODEX_HANDOFF.md').is_file())
        self.assertEqual(execute(Store(destination),'doctor')['counts'],execute(self.store,'doctor')['counts'])

    def _session(self):
        with self.store.connect(True) as c: return c.execute('SELECT id FROM learning_sessions').fetchone()[0]

    def test_corrupt_backup_and_existing_restore_target_rejected(self):
        result=create_backup(self.store);p=Path(result['path'])
        with self.assertRaises(Error): restore_backup(p,self.root,self.root)
        (p/'files/config/schedule.json').write_bytes(b'corrupt')
        with self.assertRaises(Error): verify_backup(p)

    def test_missing_handoff_does_not_block_backup_missing_evidence_does(self):
        (self.root/'subjects/linear/CODEX_HANDOFF.md').unlink()
        self.assertEqual(create_backup(self.store)['status'],'BACKUP_VERIFIED')
        data={'x':'actual report evidence'}
        self.store.run('synthetic.artifact',{'request_id':uid()},lambda raw:{'id':self.store.artifact('lessons','fixture',canonical(data))})
        with self.store.connect(True) as c: path=c.execute('SELECT path FROM artifacts').fetchone()[0]
        (self.root/path).unlink()
        with self.assertRaises(Error): create_backup(self.store)

    def test_backup_exception_not_published(self):
        with patch('studyos.maintenance.shutil.copyfile',side_effect=OSError('synthetic full disk')):
            with self.assertRaises(OSError): create_backup(self.store)
        entries=list((self.root/'backups').iterdir())
        self.assertTrue(entries);self.assertTrue(all(p.name.startswith('.partial-') for p in entries))


class FailureTests(Case):
    def test_killed_business_writer_rolls_back_original_answer_and_audit(self):
        code=r'''
from studyos.storage import Store
from studyos.learning import add_attempt
import os,sys
s=Store(sys.argv[1]);raw={'request_id':'crash-before-commit','knowledge_id':'k1','question':'合成题','answer':'合成原答',
 'process':'过程','form':'recall','source':'codex_independent','confirmed':True,'assistance':'NONE',
 'evaluation':{'result':'PASS','basis':'依据','reference':'标准'}}
def handler(r):
 add_attempt(s,r)
 os._exit(72)
s.run('attempt.add',raw,handler)
'''
        before=self.count('audit_events')
        done=subprocess.run([sys.executable,'-c',code,str(self.root)],capture_output=True)
        self.assertEqual(done.returncode,72,done.stderr)
        self.assertEqual(self.count('attempts'),0);self.assertEqual(self.count('audit_events'),before)
        self.assertTrue(execute(self.store,'doctor')['ok'])
    def test_process_interruption_config_recovery(self):
        # Kill after file replacement but before the DB transaction commits.
        code=r'''
from pathlib import Path
from unittest.mock import patch
import os,sys
from studyos.storage import Store
from studyos.app import execute
import studyos.storage as storage
s=Store(sys.argv[1]);original=storage.atomic
def crash(path,data):
 original(path,data)
 if str(path).endswith('schedule.json'): os._exit(71)
with patch('studyos.storage.atomic',crash):
 execute(s,'schedule.confirm',{'request_id':'crashed-config','expected_revision':1,'authorization_ref':'synthetic crash fixture'})
'''
        result=subprocess.run([sys.executable,'-c',code,str(self.root)],capture_output=True)
        self.assertEqual(result.returncode,71)
        self.assertTrue((self.root/'database/config-journal.json').exists())
        with self.assertRaises(Error): self.do('lesson.create',{'id':'new'})
        self.assertTrue(execute(self.store,'recover')['recovered'])
        self.assertEqual(execute(self.store,'schedule.show')['status'],'confirmed')
        self.assertTrue(execute(self.store,'doctor')['ok'])

    def test_sqlite_writer_lock_returns_error_without_partial_business(self):
        c=self.store.connect();self.addCleanup(c.close);c.execute('BEGIN IMMEDIATE')
        before=self.count('lesson_tasks')
        with self.assertRaises(sqlite3.OperationalError):
            self.do('lesson.create',{'id':'blocked','knowledge_id':'k1','goal':'g','strategy':'s','acceptance':'a','next_task':'n'})
        self.assertEqual(before,self.count('lesson_tasks'))

    def test_two_processes_same_request_create_one_record(self):
        payload={'request_id':'concurrent','id':'t','knowledge_id':'k1','goal':'g','strategy':'s','acceptance':'a','next_task':'n'}
        input_file=self.root/'request.json';input_file.write_text(canonical(payload),encoding='utf-8')
        command=[sys.executable,'-m','studyos','lesson','create','--root',str(self.root),'--input',str(input_file),'--json']
        a=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        b=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        outa,_=a.communicate(timeout=20);outb,_=b.communicate(timeout=20)
        self.assertEqual(a.returncode,0,outa);self.assertEqual(b.returncode,0,outb)
        self.assertEqual(self.count('lesson_tasks'),1)

    def test_missing_changed_material_does_not_find_replacement(self):
        self.materials();p=self.root/'synthetic_materials/outline.md'
        p.write_text('内容变化',encoding='utf-8')
        self.assertEqual(execute(self.store,'materials.check',{'subject':'math2'})[0]['status'],'MATERIAL_CHANGED')
        p.rename(p.with_name('other.md'))
        self.assertEqual(execute(self.store,'materials.check',{'subject':'math2'})[0]['status'],'MATERIAL_MISSING')


class MigrationTests(Case):
    def source(self):
        source=self.root.parent/'legacy';p=source/'study_data/tracks/legacy_exam';p.mkdir(parents=True)
        (p/'config.json').write_text(canonical({'track':'legacy_exam','goal':{'exam_year':2027},'enabled':False}),encoding='utf-8')
        return source

    def test_migration_dry_run_no_formal_change_and_apply_needs_backup_authorization(self):
        source=self.source();before=self.count('commands')
        result=execute(self.store,'migrate.dry-run',{'source':str(source)})
        self.assertTrue(result['trial']);self.assertEqual(before,self.count('commands'))
        backup=create_backup(self.store)
        info=execute(self.store,'migrate.inspect',{'source':str(source)})
        with self.assertRaises(Error): execute(self.store,'migrate.apply',{'source':str(source),'request_id':uid()})
        result=execute(self.store,'migrate.apply',{'source':str(source),'source_fingerprint':info['fingerprint'],
             'authorization_ref':'synthetic explicit approval','request_id':uid(),'backup_path':backup['path']})
        self.assertEqual(result['result']['learning_records_imported'],0)
        self.assertEqual(self.count('attempts'),0);self.assertEqual(self.store.config('schedule')['status'],'draft')

    def test_unknown_legacy_evidence_not_silently_discarded(self):
        source=self.source();p=source/'study_data/tracks/legacy_exam/state.json'
        p.write_text(canonical({'abilities':{'x':'L3'}}),encoding='utf-8')
        self.assertFalse(execute(self.store,'migrate.inspect',{'source':str(source)})['supported'])
        with self.assertRaises(Error): execute(self.store,'migrate.dry-run',{'source':str(source)})
