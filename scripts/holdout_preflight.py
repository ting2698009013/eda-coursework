"""Acquire the fixed eight unused inputs; preflight fixed scripts only, with enforced caps."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback
import urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lutsearch.abc import ABC,ROOT,sha256,validate_aiger
from lutsearch.campaign import utc
from lutsearch.search import build_reference,write_json

COMMIT='82d8cc6910419298e713a46644ed59fd3df53038'
INPUTS=[('div','arithmetic'),('hyp','arithmetic'),('log2','arithmetic'),('multiplier','arithmetic'),
        ('sqrt','arithmetic'),('square','arithmetic'),('mem_ctrl','random_control'),('voter','random_control')]
POLICY=dict(reference_command_seconds=60,cec_seconds=120,circuit_wall_seconds=600,memory_limit_bytes=8*1024**3,
    memory_semantics='RLIMIT_AS (virtual address space), stricter than RSS; enforced on every ABC process',
    eligibility='Supported input; direct/resyn2/resyn2x2 finish and both AIG and BLIF CEC pass within caps',
    candidate_selection='All eight unused regular circuits; never filtered on optimization benefit')

def download():
    target=ROOT/'manifest_holdout.json'
    if target.exists():
        manifest=json.loads(target.read_text())
        for c in manifest['circuits']:
            if sha256(ROOT/c['file'])!=c['sha256']:raise ValueError('Holdout input changed')
        return manifest
    circuits=[]
    for name,group in INPUTS:
        relative=f'{group}/{name}.aig';url=f'https://raw.githubusercontent.com/lsils/benchmarks/{COMMIT}/{relative}'
        file=ROOT/'benchmarks/holdout'/f'{name}.aig';file.parent.mkdir(exist_ok=True)
        if not file.exists():
            request=urllib.request.Request(url,headers={'User-Agent':'EDA-Course-LUT-Search'})
            with urllib.request.urlopen(request,timeout=90) as response:payload=response.read()
            file.write_bytes(payload)
        circuits.append(dict(name=name,group=group,split='new_holdout',file=file.relative_to(ROOT).as_posix(),
            url=url,sha256=sha256(file),bytes=file.stat().st_size))
    manifest=dict(benchmark_commit=COMMIT,circuits=circuits,created_utc=utc(),selection=POLICY['candidate_selection'])
    write_json(target,manifest);return manifest

def worker(name,out):
    import resource
    start=time.perf_counter();manifest=json.loads((ROOT/'manifest_holdout.json').read_text())
    c=next(c for c in manifest['circuits'] if c['name']==name)
    result=dict(circuit=name,started_utc=utc(),input_sha256=c['sha256'],policy=POLICY,eligible=False)
    out.mkdir(parents=True,exist_ok=False)
    try:
        source=ROOT/c['file'];validate_aiger(source)
        if sha256(source)!=c['sha256']:raise ValueError('Input checksum changed')
        abc=ABC(timeout=60,memory_limit_bytes=POLICY['memory_limit_bytes']);abc.deadline=start+600
        reference=build_reference(abc,source,out/'references',cec_timeout=120)
        result.update(eligible=True,reference=reference)
    except Exception as exc:
        result.update(reason=repr(exc));(out/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
    result.update(seconds=time.perf_counter()-start,completed_utc=utc(),peak_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    write_json(out/'result.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='reference'}),flush=True)

def run(out):
    manifest=download();out.mkdir(parents=True,exist_ok=False)
    write_json(out/'protocol.json',POLICY)
    write_json(out/'inputs.json',manifest)
    (out/'preflight_source.py').write_bytes(Path(__file__).read_bytes())
    records=[]
    for c in manifest['circuits']:
        name=c['name'];print('PREFLIGHT',name,flush=True)
        worker_start=time.perf_counter()
        with (out/(name+'.log')).open('w') as log:
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--worker',name,'--out',str(out/name)],
                stdout=log,stderr=log,start_new_session=True)
            try:process.wait(timeout=610)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait()
        resultfile=out/name/'result.json'
        if resultfile.exists():record=json.loads(resultfile.read_text())
        else:record=dict(circuit=name,eligible=False,reason='Worker failed or exceeded enforced wall cap',returncode=process.returncode,
                         seconds=time.perf_counter()-worker_start,peak_child_rss_note='Worker did not complete RSS recording')
        records.append(record);write_json(out/'progress.json',dict(completed=len(records),total=8,records=records))
    write_json(out/'summary.json',dict(policy=POLICY,records=records,eligible=[r['circuit'] for r in records if r['eligible']],completed_utc=utc()))
    print('ELIGIBLE',[r['circuit'] for r in records if r['eligible']],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--download-only',action='store_true');p.add_argument('--worker');p.add_argument('--out',type=Path)
    a=p.parse_args()
    if a.download_only:print(json.dumps(download(),indent=2))
    elif a.worker:worker(a.worker,a.out.resolve())
    else:run(a.out.resolve())
