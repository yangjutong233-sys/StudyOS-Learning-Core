"""Validate public JSON container types before business handlers or transactions."""
from .common import require


def object_value(value,path):
    require(isinstance(value,dict),path+' 必须为对象')


def array_value(value,path,kind=None):
    require(isinstance(value,list),path+' 必须为数组')
    if kind:require(all(isinstance(x,kind) for x in value),path+' 数组元素类型无效')


def strings(data,keys,path='input',nullable=()):
    for key in keys:
        if key in data:require(isinstance(data[key],str) or (key in nullable and data[key] is None),path+'.'+key+' 必须为字符串')


def attempt_shape(data,path='attempt'):
    object_value(data,path)
    strings(data,('id','knowledge_id','question_id','question','answer','process','form','source',
                  'assistance','occurred_at'),path)
    if 'dimensions' in data:array_value(data['dimensions'],path+'.dimensions',str)
    for key in ('confirmed','uncertain','answer_seen'):
        if key in data:
            require(type(data[key]) is bool or (key=='answer_seen' and data[key] is None),path+'.'+key+' 必须为布尔值')
    if 'evaluation' in data:
        object_value(data['evaluation'],path+'.evaluation')
        strings(data['evaluation'],('result','basis','reference','error_type','error_reason'),path+'.evaluation')


def report_shape(data):
    object_value(data,'report')
    strings(data,('knowledge_external_id',),'report')
    for key in ('covered','unfinished_training'):
        if key in data:array_value(data[key],'report.'+key,str)
    if 'attempts' in data:
        array_value(data['attempts'],'report.attempts',dict)
        for i,item in enumerate(data['attempts']):attempt_shape(item,f'report.attempts[{i}]')


def validate_request(operation,data):
    object_value(data,'input')
    strings(data,('id','request_id','knowledge_id','task_id','session_id','assessment_id','mistake_id',
                  'plan_id','subject','reason','target','path','destination','source','mode','day',
                  'authorization_ref','entity_type','name','markdown_path','json_path','path_key',
                  'material_id','semantic_review_ref','schedule_hash','source_path'),nullable=('assessment_id','session_id','task_id','mistake_id'))
    if 'user_delivered' in data:require(type(data['user_delivered']) is bool,'user_delivered必须为布尔值')
    for key in ('expected_revision','expected_item_revision','material_version','task_version','limit','cursor','level'):
        if key in data:require(type(data[key]) is int and data[key]>=0,key+' 必须为非负整数')
    for key in ('dry_run','reset'):
        if key in data:require(type(data[key]) is bool,key+' 必须为布尔值')
    for key in ('assessment_ids','attempt_ids','context_attempt_ids','members','covered','unfinished_training'):
        if key in data:array_value(data[key],key,str)
    if operation in ('attempt.add','attempt.evaluate'):attempt_shape(data)
    for key in ('catalog','blueprint','config','resolution_plan'):
        if key in data:object_value(data[key],key)
    if 'points' in data:
        array_value(data['points'],'points',dict)
        for p in data['points']:
            strings(p,('id','external_id','name','criteria','initial_state','user_statement'))
            for key in ('strong','weak'):
                if key in p:array_value(p[key],'points.'+key,str)
    if 'catalog' in data:strings(data['catalog'],('id','name','subject'),'catalog')
    if 'blueprint' in data:
        b=data['blueprint'];strings(b,('criteria','rationale'),'blueprint')
        for key in ('forms','dimensions'):
            if key in b:array_value(b[key],'blueprint.'+key,str)
        for key in ('allow_repeated_questions','allow_exposed_retest'):
            if key in b:require(type(b[key]) is bool,'blueprint.'+key+' 必须为布尔值')
        if 'delay_anchor' in b:
            object_value(b['delay_anchor'],'blueprint.delay_anchor')
            strings(b['delay_anchor'],('entity_type','entity_id'),'blueprint.delay_anchor')
    if 'bindings' in data:
        array_value(data['bindings'],'bindings',dict)
        for b in data['bindings']:
            strings(b,('knowledge_id',),'binding')
            if 'locator' in b:object_value(b['locator'],'binding.locator')
    if 'items' in data:
        array_value(data['items'],'items',dict)
        for item in data['items']:strings(item,('id','knowledge_id','review_id','start','end','goal','reason','acceptance'),'item')
    if 'config' in data:
        cfg=data['config']
        if 'subjects' in cfg:
            object_value(cfg['subjects'],'config.subjects')
            for spec in cfg['subjects'].values():
                object_value(spec,'subject')
                if 'files' in spec:
                    object_value(spec['files'],'subject.files')
                    for item in spec['files'].values():object_value(item,'material file')
        if 'week' in cfg:
            object_value(cfg['week'],'config.week')
            for spec in cfg['week'].values():object_value(spec,'week day')
        if 'dimensions' in cfg:object_value(cfg['dimensions'],'config.dimensions')
