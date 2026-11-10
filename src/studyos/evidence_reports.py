"""Immutable human-readable evidence archives, rendered from validated business facts."""
import json
from .common import canonical
from .storage import one, rows


def section(title,value):
    # Content is evidence, never executable instructions or a claim of AI semantic review.
    return '## '+title+'\n\n'+(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2))+'\n\n'


def assessment_report(store,assessment,blueprint,attempts,outcome):
    k=one(store.c,'knowledge_points',assessment['knowledge_id'])
    doc='# 独立考核报告\n\n' if blueprint['kind']=='independent' else '# 教学测试报告\n\n'
    doc+='由已保存证据生成；判定依据由Codex或教学来源提供，程序不进行语义评分。\n\n'
    doc+=section('基本信息',{'assessment_id':assessment['id'],'knowledge_id':k['external_id'],
        '知识点名称':k['name'],'评估时间':store.at,'状态':'待延迟补证据' if outcome=='DELAY_PENDING' else '已结算',
        '结果':outcome,'来源类型':blueprint['kind']})
    doc+=section('掌握标准与考核蓝图',blueprint)
    for i,a in enumerate(attempts,1):
        q=store.c.execute('SELECT * FROM questions WHERE id=? AND version=?',(a['question_id'],a['question_version'])).fetchone()
        d=json.loads(a['data'])
        ev=store.c.execute('SELECT * FROM attempt_evaluations WHERE attempt_id=? ORDER BY revision DESC LIMIT 1',(a['id'],)).fetchone()
        doc+=section(f'第{i}题：原始证据',{'attempt_id':a['id'],'题目ID':a['question_id'],'题目版本':a['question_version'],
              '题目原文':q['question'],'用户原始作答':a['answer'],'原始过程':d['process'],'提示':a['assistance'],
              '答案暴露':d.get('answer_seen'),'作答时刻':a['occurred_at'],'时刻依据':d.get('time_status','RECORDED_ONLY')})
        doc+=section('标准答案、判定与错误原因',json.loads(ev['data']))
    return store.artifact('assessments',assessment['id'],doc,suffix='.md',version=assessment['revision']+1,
                          data={'request_id':store.request,'outcome':outcome,'attempt_ids':[a['id'] for a in attempts]})


def mistake_report(store,mistake_id,reason,event_type,version):
    m=one(store.c,'mistakes',mistake_id);a=one(store.c,'attempts',m['attempt_id'])
    q=store.c.execute('SELECT * FROM questions WHERE id=? AND version=?',(a['question_id'],a['question_version'])).fetchone()
    ev=store.c.execute('SELECT * FROM attempt_evaluations WHERE attempt_id=? ORDER BY revision DESC LIMIT 1',(a['id'],)).fetchone()
    events=rows(store.c,"SELECT event_seq,event_type,occurred_at,event_payload FROM audit_events WHERE entity_type='mistake' AND entity_id=? ORDER BY event_seq",(mistake_id,))
    doc='# 错题分析与纠正记录\n\n'
    doc+=section('档案',{'mistake_id':mistake_id,'版本':version,'状态':m['status'],'错因分类':m['cause'],'事件':event_type})
    doc+=section('原始题目与作答',{'题目':q['question'],'原始答案':a['answer'],'过程':json.loads(a['data'])['process'],
                                  '提示':a['assistance'],'attempt_id':a['id']})
    doc+=section('判定及评分依据',json.loads(ev['data']))
    doc+=section('本次分析与后续方案',{'原因':reason,'方案':json.loads(m['data'])})
    doc+=section('历史变化与证据引用',events)
    return store.artifact('mistakes',mistake_id,doc,suffix='.md',version=version,
                          data={'request_id':store.request,'event_type':event_type,'attempt_id':a['id']})


def review_report(store,task,series,result,next_task):
    doc='# 复习结果报告\n\n'
    doc+=section('复习任务',{'id':task['id'],'series_id':series['id'],'kind':series['kind'],'round':task['round'],
                            '最初建议日期':task['suggested_date'],'实际安排日期':task['due_date'],
                            '本次评估时间':store.at,'实际完成':result.get('actual_completed_at')})
    doc+=section('测试结果与间隔依据',result)
    doc+=section('后续安排',next_task if next_task else '课件固定复习轮次已完成；不自动追加D60/D90。' if result.get('series_finished') else '结果无法判定，等待补证据，不自动生成成功轮次。')
    references=[]
    for assessment_id in result['assessment_ids']:
        artifacts=rows(store.c,"SELECT id,path,sha256 FROM artifacts WHERE kind='assessments' AND entity_id=?",(assessment_id,))
        references.append({'assessment_id':assessment_id,'逐题原答及评分报告':artifacts})
    doc+=section('逐题证据定位',references)
    return store.artifact('reviews',task['id'],doc,suffix='.md',version=task['revision']+1,
                          data={'request_id':store.request,'assessment_ids':result['assessment_ids']})
