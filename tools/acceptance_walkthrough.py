"""Independent public-CLI acceptance, synthetic fixtures, never learner data."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile


def run_acceptance():
    log=[]
    with tempfile.TemporaryDirectory(prefix='studyos-cli-acceptance-') as temp:
        root=Path(temp)/'work';fixtures=Path(temp)/'materials';fixtures.mkdir()
        (fixtures/'outline.md').write_text('# 合成验收大纲\nCLI-01\n掌握标准：独立解释\n习题：辨析条件\n',encoding='utf-8')
        seq=0
        def call(command,raw=None,at='2026-01-01',target=None):
            nonlocal seq
            seq+=1
            payload={'request_id':f'cli-acceptance-{seq}',**(raw or {})}
            args=[sys.executable,'-m','studyos',*command.split(),'--root',str(target or root),'--at',at,'--json','--input','-']
            result=subprocess.run(args,input=json.dumps(payload,ensure_ascii=False),text=True,encoding='utf-8',
                    capture_output=True,timeout=40,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
            record={'command':command,'exit_code':result.returncode,'at':at};log.append(record)
            assert result.returncode==0,(command,result.stdout,result.stderr)
            value=json.loads(result.stdout);assert value['ok'],value
            record['ok']=True
            data=value['data']
            return data['result'] if isinstance(data,dict) and data.get('committed') else data
        from studyos.storage import defaults
        schedule=defaults()['schedule']
        schedule['week']={str(i):{'windows':[['08:00','12:00']],'blocked':[],
            'max_minutes':240,'rest':False} for i in range(7)}
        (root/'config').mkdir(parents=True)
        (root/'config/schedule.json').write_text(json.dumps(schedule),encoding='utf-8')
        initial=call('init');assert initial['schedule']['status']=='draft'
        call('knowledge import',{'catalog':{'id':'cli-catalog','subject':'math2','name':'合成CLI验收'},
          'points':[{'id':'cli-k','external_id':'CLI-01','name':'条件辨析','criteria':'独立解释条件','location':'大纲第3行'}]})
        cfg=call('materials show');cfg['subjects']['math2']={'path':str(fixtures),'files':{'outline':{'path':'outline.md'}}}
        cfg['handoff_scopes']=[{'id':'cli','title':'合成CLI教材','subject':'math2','directory':'subjects/cli'}]
        call('materials update',{'config':cfg,'expected_revision':1,'reason':'合成验收固定材料'})
        call('materials register',{'id':'cli-outline','subject':'math2','path_key':'outline','role':'outline',
              'bindings':[{'knowledge_id':'cli-k','locator':{'line_start':1,'line_end':4}}]})
        call('lesson create',{'id':'cli-lesson','knowledge_id':'cli-k','goal':'解释条件','strategy':'原理再练习',
              'acceptance':'独立解释','next_task':'做独立验证','unfinished_training':['强化题']})
        call('lesson prepare',{'task_id':'cli-lesson'})
        assert (root/'subjects/cli/GPT_TEACHING_HANDOFF.md').exists()
        session=call('lesson start',{'task_id':'cli-lesson'})
        # Independent fixture follows the documented anchor contract; expected business outcomes are fixed below.
        report={'report_id':'cli-report','version':1,'task_id':'cli-lesson','task_version':1,'session_id':session['session_id'],
          'covered':['条件讲解'],'checkpoint':'待考核','minutes':None,'unfinished_training':['强化题'],
          'attempts':[{'id':'cli-report-attempt','question':'解释条件','answer':'合成真实原答','process':'逐步说明',
             'form':'recall','dimensions':['concept'],'assistance':'LIGHT_HINT','answer_seen':False,
             'evaluation':{'result':'PASS','basis':'大纲标准','reference':'完整条件','error_reason':'无','error_type':'UNKNOWN'}}]}
        fields={k:report[k] for k in ('report_id','version','task_id','session_id','covered','checkpoint','unfinished_training','minutes')}
        a=report['attempts'][0]
        for key in ('question','answer','process','form','dimensions','assistance','answer_seen'):
            fields[a['id']+'.'+key]=a[key]
        for key,value in a['evaluation'].items(): fields[a['id']+'.evaluation.'+key]=value
        def md_text():
            return '# 合成完整教学成果\n\n'+'\n\n'.join('<!-- studyos-field:'+k+' -->\n'+
                 (v if isinstance(v,str) else json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')))+
                 '\n<!-- /studyos-field -->' for k,v in fields.items())+'\n\n## 教学分析\n已经讲解，独立验证未进行。\n'
        md=Path(temp)/'report.md';jp=Path(temp)/'report.json'
        md.write_text(md_text().replace('合成真实原答','篡改答案'),encoding='utf-8');jp.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        pair={'markdown_path':str(md),'json_path':str(jp),'semantic_review_ref':'合成验收，非真实学习'}
        assert not call('report validate',pair)['valid']
        assert call('report import',pair)['status']=='PENDING'
        assert call('doctor')['counts']['attempts']==0
        report['version']=2;fields['version']=2
        md.write_text(md_text(),encoding='utf-8');jp.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8')
        assert call('report resolve',{**pair,'reason':'恢复原始答案'})['status']=='ACCEPTED'
        assert call('doctor')['counts']['mastery_assessments']==0
        def assessment(at):
            test=call('assessment create',{'knowledge_id':'cli-k','blueprint':{'criteria':'大纲条件','rationale':'单点解释',
                   'min_attempts':1,'forms':['recall'],'dimensions':['concept']}},at)
            call('attempt add',{'knowledge_id':'cli-k','assessment_id':test['id'],'question':'新独立题 '+str(seq),
                  'answer':'合成独立原答','process':'独立解释','form':'recall','dimensions':['concept'],'source':'codex_independent',
                  'confirmed':True,'occurred_at':at+'T00:00:00+08:00','assistance':'NONE','evaluation':{'result':'PASS','basis':'逐题标准','reference':'完整条件'}},at)
            result=call('assessment submit',{'id':test['id'],'expected_revision':1},at)
            assert result['result']=='PASS';return test['id']
        assessment('2026-01-01')
        due=call('review due',at='2026-01-02');assert len(due)==1 and due[0]['suggested_date']=='2026-01-02'
        test_id=assessment('2026-01-02')
        result=call('review complete',{'id':due[0]['id'],'expected_revision':1,'assessment_id':test_id},'2026-01-02')
        assert result['next']['suggested_date']=='2026-01-05'
        call('schedule confirm',{'expected_revision':1,'authorization_ref':'仅合成根目录的时间确认'})
        ctx=call('planning-context',{'day':'2026-01-02'})
        call('plan apply',{'id':'cli-plan','day':'2026-01-02','schedule_hash':ctx['capacity']['schedule_hash'],'items':[
          {'id':'cli-item','knowledge_id':'cli-k','start':'08:00','end':'09:00','goal':'复盘','reason':'独立验证后巩固','acceptance':'解释条件'}]})
        call('plan record-progress',{'plan_id':'cli-plan','id':'cli-item','expected_revision':1,'status':'COMPLETED','reason':'合成实际完成'})
        (root/'subjects/cli/GPT_TEACHING_HANDOFF.md').write_text('伪造快照',encoding='utf-8')
        assert call('handoff ensure',{'target':'both'})['regenerated']
        checked=call('doctor');assert checked['ok']
        backup=call('backup create',{'mode':'personal'});assert backup['restore_verified']
        destination=Path(temp)/'restored'
        restored=call('restore restore',{'path':backup['path'],'destination':str(destination)})
        assert restored['counts']==checked['counts']
        assert call('doctor',target=destination)['ok']
        return {'synthetic_only':True,'passed':True,'cli_calls':len(log),'commands':log,'expected_next_review':'2026-01-05',
                'restore_verified':True,'no_real_learning_records_written':True}


if __name__=='__main__':
    result=run_acceptance()
    output=Path(__file__).resolve().parents[1]/'reports/engineering/CLI_ACCEPTANCE.json'
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'cli_calls':result['cli_calls'],'report':str(output)},ensure_ascii=False))
