import unittest
from scripts.analyze_results import ranks, spearman


class AnalysisTests(unittest.TestCase):
    def test_ties_receive_average_ranks(self):
        self.assertEqual(ranks([30, 10, 10, 20]), [4, 1.5, 1.5, 3])
        self.assertAlmostEqual(spearman([1, 1, 2], [1, 2, 3]), 3**0.5/2)

    def test_perfect_and_undefined_correlations(self):
        self.assertAlmostEqual(spearman([3, 2, 1], [1, 2, 3]), -1)
        self.assertAlmostEqual(spearman([1, 2, 3], [1, 2, 3]), 1)
        self.assertIsNone(spearman([2, 2], [1, 3]))
        self.assertIsNone(spearman([], []))
