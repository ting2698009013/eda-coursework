import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lutsearch.abc import ABC,ROOT,default_abc,ToolError,RESYN2,Metrics
from lutsearch.campaign import make_jobs,run_experiment
from lutsearch.search import run_search,SearchConfig,build_reference
from lutsearch.statistics import paired_comparison

class DesignTests(unittest.TestCase):
    def test_local_search_rejects_incompatible_length_and_restart(self):
        for cfg in (SearchConfig(method='sequence_local',max_depth=3),SearchConfig(method='sequence_local',restart_after=0)):
            with self.assertRaises(ValueError):cfg.validate()
    def test_method_counts_contiguous_blocks_reproducible(self):
        spec=dict(circuits=['a','b'],protocols=[dict(name='t',max_calls=20)],method_runs=[
            dict(method='random',repeats=30,seed_start=100),dict(method='lut_beam',repeats=30),
            dict(method='greedy',repeats=5)],scheduling_seed=10)
        jobs=make_jobs(spec);self.assertEqual(len(jobs),130);self.assertEqual(jobs,make_jobs(spec))
        positions={}
        for i,j in enumerate(jobs):positions.setdefault(j['block_id'],[]).append(i)
        for indices in positions.values():self.assertEqual(indices,list(range(min(indices),max(indices)+1)))
        self.assertEqual({j['seed'] for j in jobs if j['method']=='random'},set(range(100,130)))

    def records(self):
        return [dict(circuit=c,block_id=f'{c}_{r}',method=m,valid_result=True,
                     winner=dict(metrics=dict(luts=value)))
                for c,n,a,b in [('small',2,90,100),('large',8,1000,1000)]
                for r in range(n) for m,value in [('lut_beam',a),('random',b)]]

    def test_equal_circuit_weight_and_paired_constant_interval(self):
        result=paired_comparison(self.records(),iterations=100)
        expected=100*(1-math.sqrt(.9))
        self.assertAlmostEqual(result['delta_percent'],expected)
        for endpoint in result['ci95_percent']:self.assertAlmostEqual(endpoint,expected)

    def test_missing_or_invalid_block_is_not_dropped(self):
        ds=self.records();self.assertEqual(paired_comparison(ds[:-1],iterations=20)['status'],'blocked')
        ds[0]['valid_result']=False
        self.assertEqual(paired_comparison(ds,iterations=20)['status'],'blocked')

@unittest.skipUnless(default_abc().is_file(),'ABC unavailable')
class CampaignIntegrationTests(unittest.TestCase):
    def test_local_search_budget_mutation_restart_reproducibility_and_replay(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);abc=ABC();src=ROOT/'benchmarks/int2float.aig'
            build_reference(abc,src,td/'ref');trajectories=[]
            for i in range(2):
                out=td/f'run{i}'
                d=run_search(abc,src,td/'ref',out,SearchConfig(method='sequence_local',seed=17,max_calls=30,restart_after=1))
                self.assertTrue(d['valid_result']);self.assertEqual(d['search_calls'],30)
                trace=[json.loads(x) for x in (out/'trace.jsonl').read_text().splitlines()]
                self.assertEqual(trace[0]['sequence'],list(RESYN2))
                self.assertTrue(all(len(t['sequence'])==10 for t in trace))
                steps=[json.loads(x) for x in (out/'local_steps.jsonl').read_text().splitlines()]
                self.assertTrue(any(s['action']=='restart' and s['status']=='ok' for s in steps))
                for s in steps:
                    if s['action']=='mutate':self.assertEqual(sum(a!=b for a,b in zip(s['sequence'],s['before_sequence'])),1)
                trajectories.append([(t['sequence'],t['metrics']) for t in trace])
                replay=abc.evaluate(src,d['winner']['sequence'],td/f'replay{i}')
                self.assertEqual(replay.metrics,Metrics(**d['winner']['metrics']))
                for suffix,result in [('aig',replay.aig),('blif',replay.blif)]:
                    self.assertEqual(abc.verify(src,result,td/f'verify{i}_{suffix}')['status'],'equivalent')
            self.assertEqual(*trajectories)

    def test_local_initialization_is_charged_and_deadline_retains_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);abc=ABC();src=ROOT/'benchmarks/ctrl.aig'
            build_reference(abc,src,td/'ref')
            d=run_search(abc,src,td/'ref',td/'out',SearchConfig(method='sequence_local',seconds=.002,max_calls=100))
            self.assertTrue(d['valid_result']);self.assertTrue(d['fallback_to_baseline'])
            self.assertLess(d['search_seconds'],.3);self.assertEqual(d['stop_reason'],'time_budget')

    def test_small_random_space_stops_after_all_unique_sequences(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);abc=ABC();src=ROOT/'benchmarks/int2float.aig'
            build_reference(abc,src,td/'ref')
            result=run_search(abc,src,td/'ref',td/'out',SearchConfig(method='random',random_length=1,max_calls=20))
            self.assertTrue(result['valid_result'])
            self.assertEqual(result['search_calls'],5)
            self.assertEqual(result['stop_reason'],'completed_space')

    def test_reference_failure_preserves_verified_predecessor_and_error_log(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);abc=ABC();src=ROOT/'benchmarks/int2float.aig';original=abc.evaluate
            def fail_second(source,sequence,directory,*args,**kwargs):
                if sequence:
                    directory.mkdir();(directory/'abc.log').write_text('simulated second-reference failure')
                    raise ToolError('intentional test failure')
                return original(source,sequence,directory,*args,**kwargs)
            with patch.object(abc,'evaluate',side_effect=fail_second):
                with self.assertRaises(ToolError):build_reference(abc,src,td/'ref')
            partial=json.loads((td/'ref/reference_partial.json').read_text())
            self.assertFalse(partial['complete']);self.assertEqual(partial['current'],'resyn2')
            first=partial['records']['unoptimized']
            self.assertEqual(first['verification']['status'],'equivalent')
            self.assertEqual(first['aig_verification']['status'],'equivalent')
            self.assertTrue((td/'ref/unoptimized/candidate.blif').is_file())
            self.assertIn('simulated', (td/'ref/resyn2/abc.log').read_text())
            self.assertFalse((td/'ref/reference.json').exists())

    def test_interrupt_resume_reruns_whole_time_block_and_rejects_identity_change(self):
        from lutsearch.campaign import run_search as original
        import zipfile
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory(dir=ROOT,prefix='.manifest-test-') as md:
            td=Path(td);spec=dict(circuits=['ctrl','int2float'],split='development',
                protocols=[dict(name='calls5',max_calls=5)],method_runs=[
                    dict(method='random',repeats=1,seed_start=100),dict(method='lut_beam',repeats=1)])
            other=Path(md)/'inputs.json';other.write_bytes((ROOT/'manifest.json').read_bytes())
            spec['manifest']=other.relative_to(ROOT).as_posix()
            config=td/'cfg.json';config.write_text(json.dumps(spec));abc=ABC();count=0
            def interrupt(*args,**kwargs):
                nonlocal count
                count+=1
                if count==2:
                    args[3].mkdir(parents=True);raise KeyboardInterrupt('simulated interruption')
                return original(*args,**kwargs)
            with patch('lutsearch.campaign.run_search',side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):run_experiment(config,td/'out',abc)
            result=run_experiment(config,td/'out',abc,resume=True)
            self.assertTrue(result['all_valid']);self.assertEqual(result['completed_trials'],4)
            self.assertEqual(len(list((td/'out/interrupted').iterdir())),2)
            with zipfile.ZipFile(td/'out/source_snapshot.zip') as snapshot:
                self.assertEqual(snapshot.read('manifest.json'),snapshot.read(spec['manifest']))
            with patch('lutsearch.campaign.source_hash',return_value='changed'):
                with self.assertRaises(ValueError):run_experiment(config,td/'out',abc,resume=True)

    def test_random_length_lineage_and_mapping_score_control(self):
        with tempfile.TemporaryDirectory() as td:
            td=Path(td);abc=ABC();src=ROOT/'benchmarks/int2float.aig'
            build_reference(abc,src,td/'ref')
            for method in ['random','aig_beam_mapped']:
                out=td/method
                d=run_search(abc,src,td/'ref',out,SearchConfig(method=method,max_calls=12,random_length=3))
                self.assertTrue(d['valid_result'])
                trace=[json.loads(x) for x in (out/'trace.jsonl').read_text().splitlines()]
                self.assertTrue(all(t['kind']=='full' for t in trace))
                if method=='random':self.assertTrue(all(len(t['sequence'])==3 for t in trace))
                else:self.assertTrue(any(t['parent_id'] and t['parent_id']>0 for t in trace))

    def test_memory_limit_is_applied_to_actual_child(self):
        import subprocess
        limit=8*1024**3
        cp=subprocess.run(['prlimit',f'--as={limit}','--','python3','-c',
            'import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])'],capture_output=True,text=True,check=True)
        self.assertEqual(int(cp.stdout),limit)
        self.assertIn('ABC',ABC(memory_limit_bytes=limit).version())
