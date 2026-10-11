import json
import tempfile
import unittest
from pathlib import Path
from lutsearch.abc import ABC, ROOT, OPS, RESYN2, default_abc, sha256
from lutsearch.search import SearchConfig, build_reference, run_search

@unittest.skipUnless(default_abc().is_file(),'local ABC is unavailable')
class RealABCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.abc=ABC()

    def test_original_and_optimized_networks_match(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);source=ROOT/'benchmarks/int2float.aig'
            c=self.abc.evaluate(source,RESYN2,td/'eval')
            self.assertGreater(c.metrics.luts,0)
            self.assertEqual(self.abc.verify(source,c.aig,td/'cec_aig')['status'],'equivalent')
            self.assertEqual(self.abc.verify(source,c.blif,td/'cec_blif')['status'],'equivalent')

    def test_real_checker_rejects_deliberate_function_change(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            # Same ports: reference is y=a AND b; corrupted circuit is y=a OR b.
            (td/'ref.blif').write_text('.model logic\n.inputs a b\n.outputs y\n.names a b y\n11 1\n.end\n')
            (td/'bad.blif').write_text('.model logic\n.inputs a b\n.outputs y\n.names a b y\n1- 1\n-1 1\n.end\n')
            self.abc.invoke('read_blif ref.blif; strash; write_aiger -s ref.aig',td)
            check=self.abc.verify(td/'ref.aig',td/'bad.blif',td/'check')
            self.assertEqual(check['status'],'not_equivalent',check['log'])

    def test_search_budget_provenance_and_replay(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);source=ROOT/'benchmarks/int2float.aig'
            build_reference(self.abc,source,td/'reference')
            d=run_search(self.abc,source,td/'reference',td/'search',SearchConfig(max_calls=12,max_depth=3))
            self.assertLessEqual(d['search_calls'],12)
            self.assertTrue(d['valid_result'])
            self.assertEqual(d['input_sha256'],sha256(source))
            self.assertLessEqual(d['winner']['metrics']['lut_levels'],d['depth_limit'])
            rc,log,_=self.abc.invoke((td/'search/winner.abc').read_text(),td/'search')
            self.assertEqual(rc,0,log)
            check=self.abc.verify(source,td/'search/replay.blif',td/'replay_check')
            self.assertEqual(check['status'],'equivalent',check['log'])
            trace=[json.loads(x) for x in (td/'search/trace.jsonl').read_text().splitlines()]
            self.assertEqual(len(trace),d['search_calls'])

    def test_deadline_keeps_verified_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);source=ROOT/'benchmarks/ctrl.aig'
            build_reference(self.abc,source,td/'reference')
            d=run_search(self.abc,source,td/'reference',td/'search',SearchConfig(seconds=0.002,max_calls=100))
            self.assertTrue(d['valid_result'])
            self.assertLess(d['search_seconds'],0.3)
            self.assertEqual(d['stop_reason'],'time_budget')

    def test_deterministic_beam_repeats_across_seeds(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);source=ROOT/'benchmarks/int2float.aig'
            build_reference(self.abc,source,td/'reference')
            traces=[]
            for seed in (0, 2):
                out=td/str(seed)
                run_search(self.abc,source,td/'reference',out,
                           SearchConfig(method='lut_beam',seed=seed,max_calls=35,max_depth=4))
                entries=[json.loads(x) for x in (out/'trace.jsonl').read_text().splitlines()]
                traces.append([(d['sequence'], d['structure_sha256'], d['metrics']) for d in entries])
            self.assertEqual(*traces)

if __name__=='__main__':unittest.main()
