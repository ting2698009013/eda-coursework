import tempfile
import unittest
from pathlib import Path
from lutsearch.abc import structure_sha256, ToolError
from lutsearch.experiment import complete_trial, preserve_interrupted, summarize_csv


class StructuralHashTests(unittest.TestCase):
    def hashes(self, first, second):
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td)/'a.aig', Path(td)/'b.aig'
            a.write_bytes(first); b.write_bytes(second)
            return structure_sha256(a), structure_sha256(b)

    def test_binary_symbols_and_timestamp_are_ignored(self):
        body = b'aig 3 2 0 1 1\n6\n\x02\x02'
        a, b = self.hashes(body+b'i0 a\nc\nwritten at 10:00\n',
                           body+b'i0 renamed\nc\nwritten at 11:00\n')
        self.assertEqual(a, b)

    def test_output_change_is_not_merged(self):
        a, b = self.hashes(b'aig 3 2 0 1 1\n6\n\x02\x02',
                           b'aig 3 2 0 1 1\n7\n\x02\x02')
        self.assertNotEqual(a, b)

    def test_ascii_comments_are_ignored(self):
        body = b'aag 3 2 0 1 1\n2\n4\n6\n6 4 2\n'
        a, b = self.hashes(body+b'c\nfirst', body+b'c\nsecond')
        self.assertEqual(a, b)

    def test_binary_delimiter_inside_gates_is_consumed(self):
        # These bytes contain a fake comment delimiter inside the gate payload.
        body = b'aig 200 198 0 1 2\n400\n\x0a\x63\x0a\x02'
        a, b = self.hashes(body+b'c\nfirst', body[:-1]+b'\x03c\nfirst')
        self.assertNotEqual(a, b)

    def test_truncated_varint_is_rejected(self):
        with self.assertRaises(ToolError):
            self.hashes(b'aig 3 2 0 1 1\n6\n\x82', b'')


class ResumeTests(unittest.TestCase):
    def test_partial_summary_is_not_counted_and_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); trial = root/'trials/partial'; trial.mkdir(parents=True)
            (trial/'summary.json').write_text('{"winner": {}}')
            self.assertFalse(complete_trial(trial/'summary.json'))
            self.assertEqual(summarize_csv(root), [])
            preserve_interrupted(trial, root)
            self.assertFalse(trial.exists())
            self.assertEqual(len(list((root/'interrupted').glob('*/summary.json'))), 1)
