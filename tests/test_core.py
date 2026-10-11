import random
import tempfile
import unittest
from pathlib import Path
from lutsearch.abc import Candidate, Metrics, ToolError, cec_status, parse_stats, validate_aiger
from lutsearch.search import objective, select_beam, SearchConfig

class ParsingTests(unittest.TestCase):
    def test_realistic_ansi_stats(self):
        log='\x1b[1;37mctrl\x1b[0m : i/o = 7/26 lat = 0 and = 108 lev = 8\nctrl : i/o = 7/26 lat = 0 nd = 29 edge = 136 aig = 316 lev = 2\n'
        self.assertEqual(parse_stats(log),Metrics(7,26,108,8,29,2))

    def test_missing_output_is_not_success(self):
        with self.assertRaises(ToolError):parse_stats('ABC command line: "print_stats".\nError: unknown command')

    def test_sequential_stats_rejected(self):
        with self.assertRaises(ToolError):parse_stats('x : i/o = 1/1 lat = 1 and = 2 lev = 1',False)

    def test_cec_outcomes(self):
        self.assertEqual(cec_status('Networks are equivalent.  Time = 0.01 sec'),'equivalent')
        self.assertEqual(cec_status('Networks are NOT EQUIVALENT.\n'),'not_equivalent')
        self.assertEqual(cec_status('Networks are equivalent.',timed_out=True),'timeout')
        self.assertEqual(cec_status('Miter computation has failed.'),'error')
        self.assertEqual(cec_status('ABC command line: "echo Networks are equivalent"'),'error')
        self.assertEqual(cec_status('Networks are equivalent.',returncode=1),'error')

    def test_latched_aiger_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.aig';p.write_bytes(b'aig 2 1 1 1 0\n')
            with self.assertRaises(ValueError):validate_aiger(p)

class SelectionTests(unittest.TestCase):
    def candidate(self,nodes,luts,depth,fingerprint,sequence):
        return Candidate(tuple(sequence),Metrics(3,1,nodes,3,luts,depth),Path('x'),Path('y'),fingerprint,0,'')

    def test_smaller_but_too_deep_is_not_accepted(self):
        feasible=Metrics(3,1,20,5,10,2);bad=Metrics(3,1,10,4,5,3)
        self.assertLess(objective(feasible,2),objective(bad,2))

    def test_equal_statistics_do_not_merge_structures(self):
        a=self.candidate(9,4,2,'AAA',['balance']);b=self.candidate(9,4,2,'BBB',['rewrite'])
        self.assertEqual(len(select_beam([a,b],4,2,'lut_beam',random.Random(0))),2)

    def test_exact_duplicate_is_removed(self):
        a=self.candidate(9,4,2,'AAA',['balance']);b=self.candidate(9,4,2,'AAA',['rewrite'])
        self.assertEqual(len(select_beam([a,b],4,2,'lut_beam',random.Random(0))),1)

    def test_aig_and_lut_rankings_can_disagree(self):
        a=self.candidate(9,6,2,'AAA',['balance']);b=self.candidate(12,4,2,'BBB',['rewrite'])
        self.assertIs(select_beam([a,b],1,2,'aig_beam',random.Random(0))[0],a)
        self.assertIs(select_beam([a,b],1,2,'lut_beam',random.Random(0))[0],b)

    def test_invalid_budget_rejected(self):
        with self.assertRaises(ValueError):SearchConfig(seconds=0).validate()
        with self.assertRaises(ValueError):SearchConfig(max_calls=0).validate()

if __name__=='__main__':unittest.main()
