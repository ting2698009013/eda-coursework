import unittest
from lutsearch.report import aggregate

def trial(circuit,method,luts,base=100,valid=True):
    return {'protocol':'p','method':method,'circuit':circuit,'valid_result':valid,
            'winner':{'metrics':{'luts':luts}},'baseline':{'metrics':{'luts':base}},
            'search_seconds':1.0,'search_calls':10}

class ReportingTests(unittest.TestCase):
    def test_geomean_weights_circuits_not_their_size(self):
        rows=[trial('small','random',50,100),trial('large','random',2000,2000)]
        d=aggregate(rows,['small','large'],['random'],'p')[0]
        self.assertAlmostEqual(d['geomean_gain'],100*(1-0.5**0.5))
        self.assertEqual(d['improved_circuits'],1)

    def test_uses_median_over_seeds(self):
        rows=[trial('a','random',x) for x in [50,80,100]]
        d=aggregate(rows,['a'],['random'],'p')[0]
        self.assertEqual(d['median_luts']['a'],80)
        self.assertAlmostEqual(d['geomean_gain'],20)

    def test_unverified_results_excluded(self):
        rows=[trial('a','random',90),trial('a','random',1,valid=False)]
        d=aggregate(rows,['a'],['random'],'p')[0]
        self.assertEqual(d['median_luts']['a'],90)

if __name__=='__main__':unittest.main()
