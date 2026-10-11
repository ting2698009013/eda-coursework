"""Read-only consistency audit of completed experiments, independent of report generation."""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lutsearch.abc import structure_sha256

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def audit(run):
    meta=json.loads((run/'experiment.json').read_text(encoding='utf-8'))
    completion=json.loads((run/'completion.json').read_text(encoding='utf-8'))
    spec=meta['identity']['spec']
    if 'method_runs' in spec:
        expected={f'{c}__{p["name"]}__{m.get("label",m["method"])}__r{r}'
                  for c in spec['circuits'] for p in spec['protocols'] for m in spec['method_runs'] for r in range(m['repeats'])}
    else:
        expected={f'{c}__{p["name"]}__{m}__s{s}' for c,p,m,s in itertools.product(spec['circuits'],spec['protocols'],spec['methods'],spec['seeds'])}
    summaries=list((run/'trials').glob('*/summary.json'))
    actual={p.parent.name for p in summaries}
    problems=[]
    with zipfile.ZipFile(run/'source_snapshot.zip') as snapshot:
        h=hashlib.sha256()
        for name in sorted(n for n in snapshot.namelist() if n.startswith('lutsearch/') and n.endswith('.py')):
            h.update(Path(name).name.encode());h.update(snapshot.read(name))
        if h.hexdigest()!=meta['identity']['source_sha256']:
            problems.append({'trial':'ALL','problem':'source snapshot checksum mismatch'})
        manifest_name=spec.get('manifest','manifest.json')
        if manifest_name not in snapshot.namelist() or hashlib.sha256(snapshot.read(manifest_name)).hexdigest()!=meta['identity']['manifest_sha256']:
            problems.append({'trial':'ALL','problem':'selected manifest snapshot mismatch'})
        if json.loads(snapshot.read('experiment_config.json'))!=spec:
            problems.append({'trial':'ALL','problem':'configuration snapshot mismatch'})
    deterministic={}
    if expected!=actual:problems.append({'trial':'ALL','problem':'planned and recorded trial identities differ'})
    for file in summaries:
        directory=file.parent;d=json.loads(file.read_text(encoding='utf-8'));name=directory.name
        def check(condition,message):
            if not condition:problems.append({'trial':name,'problem':message})
        check(d['input_sha256']==digest(directory/'input.aig'),'input checksum mismatch')
        check(d['winner']['aig_sha256']==digest(directory/'winner.aig'),'winner AIG checksum mismatch')
        check(d['winner']['structure_sha256']==structure_sha256(directory/'winner.aig'),'winner structure checksum mismatch')
        check(d['abc_sha256']==meta['identity']['abc_sha256'],'ABC binary mismatch')
        check(d['valid_result'] and d['verified'],'unverified or infeasible final result')
        check(d['winner']['metrics']['lut_levels']<=d['depth_limit'],'depth constraint violated')
        check(d['search_calls']<=d['config']['max_calls'],'call limit exceeded')
        if d['config']['seconds'] is not None:
            check(d['search_seconds']<=d['config']['seconds']+0.3,'time limit substantially exceeded')
        ref=json.loads((run/'references'/d['circuit']/'reference.json').read_text(encoding='utf-8'))
        check(d['baseline']['metrics']==ref['records']['resyn2']['metrics'],'reference metrics mismatch')
        b=d['baseline']['metrics']['luts'];a=d['winner']['metrics']['luts']
        check(abs(d['gain_percent']-100*(b-a)/max(1,b))<1e-9,'reported gain mismatch')
        trace=[json.loads(x) for x in (directory/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
        if d.get('algorithm_method',d['method'])=='sequence_local':
            local=[json.loads(x) for x in (directory/'local_steps.jsonl').read_text(encoding='utf-8').splitlines()]
            check(bool(local) and local[0]['action']=='initialize','missing local initialization record')
            check(all(len(t['sequence'])==10 for t in trace),'local search changed sequence length')
            by_call={t['call']:t for t in trace}
            for s in local:
                if s['action']=='mutate':
                    check(sum(a!=b for a,b in zip(s['sequence'],s['before_sequence']))==1,'mutation changed more than one operation')
                if s.get('candidate_call') is not None:
                    check(s['candidate_call'] in by_call and by_call[s['candidate_call']]['sequence']==s['sequence'],'local step/trace mismatch')
        if (directory/'selection.jsonl').exists():
            selections=[json.loads(x) for x in (directory/'selection.jsonl').read_text(encoding='utf-8').splitlines()]
            for s in selections:
                check(set(s['selected_calls']).issubset(s['pool_calls']),'selected candidate outside pool')
            check(all(t.get('parent_id') is None or t['parent_id']<t['call'] for t in trace),'invalid parent lineage')
        check([x['call'] for x in trace]==list(range(1,d['search_calls']+1)),'trace call sequence incomplete')
        times=[x['elapsed_seconds'] for x in trace]
        check(times==sorted(times),'trace elapsed time is not monotonic')
        check(all(x.get('status')!='tool_error' for x in trace),'unexpected ABC tool error in search')
        if d['config']['seconds'] is None and d['method'] in ('greedy','aig_beam','lut_beam'):
            key=(d['circuit'],d['method'])
            trajectory=[(x['sequence'],x['kind'],x.get('metrics'),x.get('structure_sha256'),x['status']) for x in trace]
            if key in deterministic:
                check(trajectory==deterministic[key],'deterministic search differs between repetitions')
            else:
                deterministic[key]=trajectory
        check(sum(x['status']=='ok' and x['kind']!='aig_only' for x in trace)==d['mapping_evaluations'],'mapping count mismatch')
        check(all(trace[i]['incumbent_luts']<=trace[i-1]['incumbent_luts'] for i in range(1,len(trace))),'incumbent resource count increased')
        for suffix in ['aig','blif']:
            log=(directory/f'cec_{suffix}.log').read_text(encoding='utf-8')
            check(bool(re.search(r'^Networks are equivalent',log,re.M)),f'{suffix} does not contain an explicit CEC success')
            check(d['verification'][suffix]['status']=='equivalent',f'{suffix} saved CEC status is not equivalent')
        check(d['lut_size']==meta['identity']['lut_size'],'LUT model differs from experiment')
        script=(directory/'winner.abc').read_text(encoding='utf-8')
        check('write_aiger -s replay.aig' in script,'replay script loses port names')
    check_count=len(summaries)
    if completion['completed_trials']!=check_count:problems.append({'trial':'ALL','problem':'completion count mismatch'})
    return {'planned_trials':len(expected),'audited_trials':check_count,'passed':not problems,'problems':problems}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);p.add_argument('--out',type=Path);a=p.parse_args()
    result=audit(a.run.resolve());text=json.dumps(result,ensure_ascii=False,indent=2);print(text)
    if a.out:a.out.write_text(text+'\n',encoding='utf-8')
    raise SystemExit(0 if result['passed'] else 2)
