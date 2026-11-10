import unittest
from tools.acceptance_walkthrough import run_acceptance


class PublicCliAcceptance(unittest.TestCase):
    def test_public_cli_learning_report_review_plan_and_restore(self):
        result=run_acceptance()
        self.assertTrue(result['passed'])
        self.assertGreater(result['cli_calls'],25)
        self.assertEqual(result['expected_next_review'],'2026-01-05')
        self.assertTrue(result['restore_verified'])
