"""Replay saved scripts, comparing exact metrics and checking both output formats."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lutsearch.abc import ABC, parse_stats
from lutsearch.search import write_json

def replay_one(abc,folder,logs,spec):
    """Each trial is independent; an exception must not prevent the remaining replays."""
    try:
        d=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='lut-replay-') as td:
            td=Path(td);shutil.copyfile(folder/'input.aig',td/'input.aig')
            script=(folder/'winner.abc').read_text(encoding='utf-8').replace('write_aiger -s','print_stats; write_aiger -s')
            rc,log,_=abc.invoke(script,td,timeout=max(30,spec.get('reference_command_timeout',30)))
            (logs/(folder.name+'.log')).write_text(log,encoding='utf-8')
            metrics=asdict(parse_stats(log));statuses={}
            for suffix in ('aig','blif'):
                result=abc.verify(td/'input.aig',td/('replay.'+suffix),td/('cec_'+suffix),d['config'].get('cec_timeout',30))
                statuses[suffix]=result['status']
                (logs/(folder.name+'.cec_'+suffix+'.log')).write_text(result['log'],encoding='utf-8')
            return dict(trial=folder.name,metrics_match=metrics==d['winner']['metrics'],
                equivalent=all(v=='equivalent' for v in statuses.values()),verification=statuses,
                returncode=rc,replayed_metrics=metrics)
    except Exception as exc:
        (logs/(folder.name+'.error.log')).write_text(repr(exc),encoding='utf-8')
        partial=getattr(exc,'stdout',None)
        if partial:
            (logs/(folder.name+'.partial.log')).write_text(partial.decode('utf-8',errors='replace') if isinstance(partial,bytes) else str(partial),encoding='utf-8')
        return dict(trial=folder.name,metrics_match=False,equivalent=False,returncode=None,error=repr(exc))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path)
    parser.add_argument('--all',action='store_true',help='replay every trial instead of five examples')
    args=parser.parse_args()
    run=args.run.resolve();abc=ABC();checks=[]
    spec=json.loads((run/'experiment.json').read_text(encoding='utf-8'))['identity']['spec']
    abc.memory_limit_bytes=spec.get('memory_limit_bytes')
    chosen={'random':'cavlc','greedy':'i2c','aig_beam':'adder','lut_beam':'router','lut_beam_diverse':'priority'}
    folders=sorted(p.parent for p in (run/'trials').glob('*/summary.json')) if args.all else [
        run/'trials'/f'{circuit}__calls120__{method}__s0' for method,circuit in chosen.items()]
    logs=run/'replay_logs';logs.mkdir(exist_ok=True)
    for index,folder in enumerate(folders,1):
        record=replay_one(abc,folder,logs,spec);checks.append(record)
        if not record['metrics_match'] or not record['equivalent']:print('REPLAY MISMATCH',record,flush=True)
        if index%25==0 or not args.all or index==len(folders):print(f'Replayed {index}/{len(folders)}',flush=True)
        write_json(run/'replay_progress.json',dict(completed=index,planned=len(folders),failed=sum(not r['equivalent'] or not r['metrics_match'] or r['returncode']!=0 for r in checks)))
    result={'passed':bool(checks) and all(r['metrics_match'] and r['equivalent'] and r['returncode']==0 for r in checks),'checks':checks}
    write_json(run/'replay_audit.json',result)
    raise SystemExit(0 if result['passed'] else 2)

if __name__=='__main__':main()
