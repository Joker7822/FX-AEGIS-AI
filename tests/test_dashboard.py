import json
import tempfile
import unittest
from pathlib import Path
from dashboard import build_payload

ROOT = Path(__file__).resolve().parents[1]

class DashboardTests(unittest.TestCase):
    def test_test_segment_uses_only_test_segment(self):
        result = build_payload(ROOT/'results/demo_trend', ROOT/'data/demo_synthetic_usdjpy.csv', 'test')
        self.assertEqual(result['stats']['trade_count'], 14)
        self.assertEqual(len(result['trade_markers']), 14)
        self.assertEqual(result['last_signal']['side'], 0)
        self.assertTrue(result['market'])
        self.assertLessEqual(result['market'][0]['t'], result['start'].replace(' ', 'T'))

    def test_validation_segment_is_independent(self):
        result = build_payload(ROOT/'results/demo_trend', ROOT/'data/demo_synthetic_usdjpy.csv', 'validation')
        self.assertEqual(result['stats']['trade_count'], 18)
        self.assertAlmostEqual(result['stats']['net_profit_yen'], -98.25)

    def test_research_snapshot_optional(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d)/'research.json'
            f.write_text(json.dumps({'retrieved_at_utc':'2026-11-13T00:00:00Z','trusted_official_hits':[{'title':'FOMC calendar','source_url':'https://www.federalreserve.gov'}]}), encoding='utf-8')
            result = build_payload(ROOT/'results/demo_trend', ROOT/'data/demo_synthetic_usdjpy.csv', 'test', f)
            self.assertEqual(result['online_research']['official_hit_count'],1)
            self.assertEqual(result['online_research']['last_headline'],'FOMC calendar')

if __name__ == '__main__': unittest.main()


class EvolutionDashboardTests(unittest.TestCase):
    def test_evolution_report_is_read_only_and_optional(self):
        from dashboard import build_payload
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            t=Path(td)
            (t/'summary.json').write_text(json.dumps({'test_20pct':{'start_utc':'2023-01-01T00:00:00Z','end_utc':'2023-01-01T02:00:00Z'}}))
            (t/'report.json').write_text(json.dumps({
                'selected_blueprint':{'name':'EvolvedAgent'},'candidate_count':8,
                'promotion_eligible':False,'paper_promoted':False,
                'validation':{'net_yen':10,'stress_2x_net_yen':-20}
            }))
            p=build_payload(t,t/'missing.csv','test',evolution_report=t/'report.json')
            self.assertTrue(p['evolution']['available'])
            self.assertEqual(p['evolution']['candidate_name'],'EvolvedAgent')
            self.assertFalse(p['evolution']['paper_promoted'])
            self.assertEqual(p['evolution']['double_cost_net_yen'],-20)
