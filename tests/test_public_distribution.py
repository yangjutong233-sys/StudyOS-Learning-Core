import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from studyos.app import execute
from studyos.common import Error
from studyos.storage import Store
from tools.setup import initialize
from tools.check_public_tree import audit


class PublicSetupTests(unittest.TestCase):
    def test_empty_setup_has_no_learning_and_no_personal_schedule(self):
        with tempfile.TemporaryDirectory(prefix='studyos-public-test-') as temp:
            root = Path(temp) / 'archive'
            result = initialize(root)
            self.assertEqual(result['learning_records'], 0)
            store = Store(root)
            state = execute(store, 'summary')
            self.assertIsNone(state['selected_task'])
            self.assertEqual(state['knowledge_counts'], [])
            self.assertEqual(state['learning_reports']['accepted_user_reports'], 0)
            schedule = store.config('schedule')
            self.assertEqual(schedule['status'], 'draft')
            self.assertTrue(all(not day['windows'] and day['max_minutes'] == 0
                                for day in schedule['week'].values()))
            cfg = store.config('materials')
            self.assertEqual(cfg['learning_control']['daily_planning'], 'USER_ONLY')
            self.assertTrue(cfg['report_policy']['user_delivered_is_authentic'])
            self.assertEqual(len(list((root / 'subjects').rglob('*HANDOFF.md'))), 8)
            self.assertEqual(execute(store, 'doctor')['issues'], [])

    def test_setup_refuses_existing_content_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory(prefix='studyos-public-test-') as temp:
            root = Path(temp)
            marker = root / 'keep.txt'
            marker.write_bytes(b'original bytes')
            with self.assertRaises(Error):
                initialize(root)
            self.assertEqual(marker.read_bytes(), b'original bytes')
            self.assertFalse((root / 'database').exists())

    def test_report_intake_works_without_materials_schedule_or_knowledge(self):
        with tempfile.TemporaryDirectory(prefix='studyos-public-test-') as temp:
            root = Path(temp) / 'archive'
            initialize(root)
            source = Path(temp) / 'learner-report.txt'
            source.write_text('Synthetic learner report: studied a concept, association unknown.', encoding='utf-8')
            result = execute(Store(root), 'report.receive', {'request_id': 'synthetic-intake',
                'source_path': str(source), 'source_ref': 'Synthetic test report',
                'reported_summary': 'Studied a concept; independent assessment pending',
                'reported_facts': {'covered': ['Synthetic concept']}, 'user_delivered': True})['result']
            self.assertEqual(result['status'], 'ACCEPTED')
            self.assertEqual(result['association_status'], 'UNASSIGNED')
            self.assertEqual(execute(Store(root), 'summary')['learning_time']['confirmed_minutes'], 0)


class PublicationGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='studyos-publication-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'test@example.invalid')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, check=True)

    def test_checks_index_not_unstaged_worktree(self):
        p = self.root / 'README.md'
        p.write_text('Public documentation', encoding='utf-8')
        self.git('add', 'README.md')
        p.write_text('ghp_' + 'x' * 36, encoding='utf-8')
        self.assertTrue(audit(self.root)['ok'])
        self.git('add', 'README.md')
        self.assertFalse(audit(self.root)['ok'])

    def test_rejects_private_runtime_content_even_if_text(self):
        folder = self.root / 'snapshots'
        folder.mkdir()
        (folder / 'report.md').write_text('Synthetic report', encoding='utf-8')
        self.git('add', 'snapshots/report.md')
        self.assertFalse(audit(self.root)['ok'])

    def test_checks_deleted_data_in_history(self):
        p = self.root / 'README.md'
        p.write_text('ghp_' + 'x' * 36, encoding='utf-8')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'Synthetic rejected history')
        p.write_text('Clean current documentation', encoding='utf-8')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'Synthetic clean current tree')
        self.assertTrue(audit(self.root)['ok'])
        self.assertFalse(audit(self.root, all_history=True)['ok'])
