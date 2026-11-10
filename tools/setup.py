"""Create a new empty learner-directed archive. Never reads another archive."""
import argparse
import json
from pathlib import Path

from studyos.app import execute
from studyos.common import Error
from studyos.storage import Store, defaults


def initialize(root):
    root = Path(root).expanduser().resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise Error('TARGET_EXISTS', 'Setup requires a new, empty directory; existing content is preserved.')
    root.mkdir(parents=True, exist_ok=True)
    cfg = defaults()['materials']
    cfg['learning_control'] = {'daily_planning': 'USER_ONLY', 'gpt_handoff': 'PROGRESS_ONLY'}
    cfg['report_policy'] = {'formats': 'ANY', 'user_delivered_is_authentic': True,
                            'independent_mastery_requires_test': True}
    cfg['handoff_scopes'] = [
        {'id': scope, 'title': title, 'subject': subject, 'directory': 'subjects/' + scope}
        for scope, title, subject in [('math', 'Mathematics', 'math2'),
            ('computing', 'Computing', 'cs408'), ('english', 'English', 'english1'),
            ('politics', 'Politics', 'politics101')]]
    cfg['curriculum_guidance'] = (
        'The learner chooses content, sequence and workload. Reports in any format record real study, '
        'not independent mastery. Unknown evidence stays unknown. GPT handoffs are short progress '
        'summaries; full answers and preferences stay in the local archive. Reviews are reminders.')
    review = defaults()['review_policy']
    review['subject_overrides'] = {'english1': {'basis': 'first_learning_date',
        'intervals': [1, 3, 7, 14, 30], 'failure_days': 1, 'partial_days': 1, 'recovery_passes': 2}}
    folder = root / 'config'
    folder.mkdir()
    for name, value in [('materials', cfg), ('review_policy', review)]:
        with (folder / (name + '.json')).open('x', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
    # Pre-initialization templates become registered configuration during explicit init.
    # Subsequent configuration changes must use the public CLI.
    result = execute(Store(root), 'init')
    return {'root': str(root), 'initialized': result['initialized'],
            'schedule': result['schedule']['status'], 'learning_records': 0,
            'scope_ids': [scope['id'] for scope in cfg['handoff_scopes']]}


def main():
    parser = argparse.ArgumentParser(description='Initialize an empty local StudyOS archive')
    parser.add_argument('--root', required=True, help='New, empty local data directory')
    args = parser.parse_args()
    try:
        result = initialize(args.root)
    except (Error, OSError) as e:
        print(json.dumps({'ok': False, 'error': {'code': getattr(e, 'code', 'STORAGE_ERROR'),
            'message': str(e)}}, ensure_ascii=False))
        return 2
    print(json.dumps({'ok': True, 'data': result}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
