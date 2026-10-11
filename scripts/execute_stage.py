"""Run one frozen stage, then audit, fully replay and analyze it (sequentially)."""
import argparse
import contextlib
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lutsearch.abc import ABC,ROOT
from lutsearch.campaign import run_experiment,utc
from lutsearch.search import write_json
from audit_results import audit
from campaign_analysis import analyze

def main():
    p=argparse.ArgumentParser();p.add_argument('config',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--resume',action='store_true');a=p.parse_args()
    out=a.output.resolve();out.parent.mkdir(parents=True,exist_ok=True)
    logpath=out.parent/(out.name+'.log');started=utc()
    print('START',out.name,started,flush=True)
    with logpath.open('a',encoding='utf-8',buffering=1) as log,contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        completion=run_experiment(a.config.resolve(),out,ABC(),a.resume)
        result=audit(out);write_json(out/'audit.json',result)
        write_json(out/'phase.json',dict(phase='replay',updated_utc=utc()))
        replay=subprocess.run([sys.executable,str(ROOT/'scripts/replay_results.py'),str(out),'--all'],stdout=log,stderr=log,check=False)
        write_json(out/'phase.json',dict(phase='analysis',updated_utc=utc()))
        analysis=analyze(out)
        valid=completion['all_valid'] and result['passed'] and replay.returncode==0
        write_json(out/'phase.json',dict(phase='complete' if valid else 'complete_with_issues',started_utc=started,completed_utc=utc()))
    print('COMPLETE' if valid else 'COMPLETE_WITH_ISSUES',out.name,json.dumps(analysis['primary'],ensure_ascii=False),flush=True)
    if not valid:raise SystemExit(2)

if __name__=='__main__':main()
