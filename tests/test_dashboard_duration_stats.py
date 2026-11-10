from studyos.app import execute
from tests.support import Case
from tests import test_narrative_report as narrative
from tests import test_user_learning_reports as user_reports
from tools.dashboard import collect, render


class DashboardDurationStatsTests(Case):
    fixture=narrative.NarrativeReportTests.fixture
    raw_formal=user_reports.UserLearningReportTests.raw_formal

    def test_correcting_an_older_session_does_not_move_the_teaching_checkpoint_back(self):
        raw=self.raw_formal()
        first=self.do('report.receive',raw)
        second=self.root/'second.txt'
        second.write_text('第二课学习报告',encoding='utf-8')
        raw={**raw,'source_path':str(second),'knowledge_id':'k2','reported_summary':'第二课当前断点'}
        self.do('report.receive',raw)
        self.do('session.duration',{'session_id':first['session_id'],'minutes':15,
            'reason':'补录早期会话','expected_revision':1})
        packet=(self.root/'subjects/linear/GPT_TEACHING_HANDOFF.md').read_text(encoding='utf-8')
        checkpoint=packet.split('## 学习断点\n\n',1)[1].split('\n\n',1)[0]
        self.assertEqual(checkpoint,'第二课当前断点')

    def test_repeated_duration_corrections_replace_stats_and_recent_rows(self):
        result=self.do('report.receive',self.raw_formal())
        before=collect(self.root)
        self.assertEqual((before['date_only'],before['unknown_minutes'],before['confirmed_minutes']), (1,1,0))
        for revision,minutes in ((1,45),(2,60),(3,30)):
            self.do('session.duration',{'session_id':result['session_id'],'minutes':minutes,
                'reason':'用户更正时长','expected_revision':revision})
            data=collect(self.root)
            self.assertEqual((data['date_only'],data['unknown_minutes'],data['confirmed_minutes']), (1,0,minutes))
            self.assertEqual(len(data['recent']),1)
            self.assertEqual(data['recent'][0]['minutes'],minutes)
            self.assertEqual(data['recent'][0]['time_precision'],'DATE_ONLY')
            self.assertEqual(execute(self.store,'summary')['learning_time']['confirmed_minutes'],minutes)
        self.assertEqual(self.count('session_records'),4)
        page=render(data)
        self.assertIn('color-scheme:light',page)
        self.assertIn('.duration-form option{background:#fff;color:#26364b}',page)

    def test_unknown_date_is_not_fabricated_by_duration_correction(self):
        result=self.do('report.receive',self.raw_formal(date=None))
        self.do('session.duration',{'session_id':result['session_id'],'minutes':15,
            'reason':'用户补录','expected_revision':1})
        data=collect(self.root)
        self.assertEqual((data['date_only'],data['unknown_minutes'],data['confirmed_minutes']), (1,0,15))
        self.assertEqual(data['recent'][0]['time_precision'],'UNKNOWN')
