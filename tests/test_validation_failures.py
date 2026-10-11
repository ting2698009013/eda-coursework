"""Failure-path checks use fake tool boundaries; they never start ABC during timing runs."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from scripts.replay_results import replay_one
from scripts.campaign_analysis import analyze

class ValidationFailureTests(unittest.TestCase):
    def test_failed_replay_does_not_prevent_next_trial_and_preserves_timeout_output(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);logs=td/'logs';logs.mkdir();folders=[]
            metrics=dict(inputs=2,outputs=1,and_nodes=1,aig_levels=1,luts=1,lut_levels=1)
            for name in ('bad','timeout','good'):
                f=td/name;f.mkdir();folders.append(f)
                (f/'summary.json').write_text(json.dumps(dict(winner=dict(metrics=metrics),config={})))
                (f/'input.aig').write_bytes(b'test fixture');(f/'winner.abc').write_text('write_aiger -s replay.aig')
            (folders[0]/'summary.json').write_text('broken JSON')
            abc=Mock();abc.invoke.side_effect=[subprocess.TimeoutExpired('test',1,output=b'partial tool log'),
                (0,'c : i/o = 2/1 lat = 0 and = 1 lev = 1\nc : i/o = 2/1 lat = 0 nd = 1 lev = 1\n',.01)]
            abc.verify.return_value=dict(status='equivalent',log='Networks are equivalent',seconds=.01)
            results=[replay_one(abc,f,logs,{}) for f in folders]
            self.assertFalse(results[0]['equivalent']);self.assertFalse(results[1]['equivalent'])
            self.assertTrue(results[2]['equivalent']);self.assertTrue(results[2]['metrics_match'])
            self.assertIn('partial tool log',(logs/'timeout.partial.log').read_text())

    def test_failed_audit_blocks_primary_interval_even_if_summaries_claim_valid(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td)
            for name,data in [('protocol.json',dict(protocols=[dict(name='time10s')])),
                ('completion.json',dict(all_valid=True)),('audit.json',dict(passed=False)),('replay_audit.json',dict(passed=True))]:
                (td/name).write_text(json.dumps(data))
            self.assertEqual(analyze(td)['primary']['time10s']['status'],'blocked')
