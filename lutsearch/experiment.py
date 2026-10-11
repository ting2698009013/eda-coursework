"""Pinned benchmark experiments, sequential timing, immutable resume checks."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import csv
import hashlib
import itertools
import json
import platform
import random
import sys
import zipfile
from .abc import ABC, ROOT, sha256
from .search import SearchConfig, build_reference, run_search, write_json

def source_hash():
    h = hashlib.sha256()
    for p in sorted((ROOT/'lutsearch').glob('*.py')):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()

def complete_trial(path):
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return False
    return all(key in data for key in ('circuit', 'split', 'protocol', 'valid_result', 'winner'))

def preserve_interrupted(path, output):
    # Keep partial runs outside trials/references so report discovery cannot count them.
    archive = output/'interrupted'
    archive.mkdir(exist_ok=True)
    path.rename(archive/(path.name+'.'+datetime.now().strftime('%Y%m%d%H%M%S%f')))

def summarize_csv(directory):
    rows=[]
    for f in sorted(directory.glob('trials/*/summary.json')):
        if not complete_trial(f):
            continue
        d=json.loads(f.read_text(encoding='utf-8'))
        m=d['winner']['metrics']; b=d['baseline']['metrics']
        rows.append({'trial':f.parent.name,'circuit':d['circuit'],'split':d['split'],
            'protocol':d['protocol'],'method':d['method'],'seed':d['seed'],
            'replicate_id':d.get('replicate_id',''),'block_id':d.get('block_id',''),
            'algorithm_method':d.get('algorithm_method',d['method']),
            'baseline_luts':b['luts'],'baseline_levels':b['lut_levels'],
            'luts':m['luts'],'levels':m['lut_levels'],'gain_percent':d['gain_percent'],
            'search_seconds':d['search_seconds'],'search_calls':d['search_calls'],
            'mapping_evaluations':d['mapping_evaluations'],'valid_result':d['valid_result'],
            'cec_aig':d['verification']['aig']['status'],'cec_blif':d['verification']['blif']['status'],
            'fallback_to_baseline':d['fallback_to_baseline'],'stop_reason':d['stop_reason']})
    if rows:
        with (directory/'summary.csv').open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return rows

def run_experiment(config_path: Path, output: Path, abc: ABC, resume=False):
    spec=json.loads(config_path.read_text(encoding='utf-8'))
    manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
    circuits={c['name']:c for c in manifest['circuits']}
    selected=[circuits[n] for n in spec['circuits']]
    identity={'spec':spec,'abc_sha256':sha256(abc.binary),'source_sha256':source_hash(),
              'manifest_sha256':sha256(ROOT/'manifest.json'),'lut_size':abc.lut_size}
    if output.exists():
        if not resume:
            raise FileExistsError('Use a new directory or --resume with an unchanged experiment')
        metadata=json.loads((output/'experiment.json').read_text(encoding='utf-8'))
        if metadata['identity'] != identity:
            raise ValueError('Cannot resume: config, code, tool or input manifest changed')
    else:
        output.mkdir(parents=True)
        metadata={'identity':identity,'started_utc':datetime.now(timezone.utc).isoformat(),
            'python':sys.version,'platform':platform.platform(),'abc_version':abc.version(),
            'benchmark_commit':manifest['benchmark_commit'],
            'methodology':'All trials run sequentially. Trial order is shuffled with a fixed scheduling seed. No benchmark-level outcome filtering.'}
        write_json(output/'experiment.json',metadata)
        with zipfile.ZipFile(output/'source_snapshot.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for source in sorted((ROOT/'lutsearch').glob('*.py')):
                archive.write(source,'lutsearch/'+source.name)
            archive.write(config_path,'experiment_config.json')
            archive.write(ROOT/'manifest.json','manifest.json')
    for c in selected:
        source=ROOT/c['file']
        if sha256(source)!=c['sha256']:
            raise ValueError('Benchmark checksum changed: '+c['name'])
        ref=output/'references'/c['name']
        if not (ref/'reference.json').is_file():
            if ref.exists():
                preserve_interrupted(ref, output)
            print('REFERENCE',c['name'],flush=True)
            build_reference(abc,source,ref)
    jobs=list(itertools.product(selected,spec['protocols'],spec['methods'],spec['seeds']))
    random.Random(spec.get('scheduling_seed',20261009)).shuffle(jobs)
    failures=[]
    for index,(c,protocol,method,seed) in enumerate(jobs,1):
        name=f'{c["name"]}__{protocol["name"]}__{method}__s{seed}'
        trial=output/'trials'/name
        if resume and complete_trial(trial/'summary.json'):
            continue
        if trial.exists():
            # Preserve interrupted traces instead of deleting them.
            preserve_interrupted(trial, output)
        cfg=SearchConfig(method=method,seed=seed,max_calls=protocol['max_calls'],
            seconds=protocol.get('seconds'),max_depth=spec.get('max_depth',10),
            beam_width=spec.get('beam_width',4),command_timeout=spec.get('command_timeout',15),
            cec_timeout=spec.get('cec_timeout',30))
        print(f'[{index}/{len(jobs)}] {name}',flush=True)
        try:
            d=run_search(abc,ROOT/c['file'],output/'references'/c['name'],trial,cfg)
            d.update(circuit=c['name'],split=c['split'],protocol=protocol['name'])
            write_json(trial/'summary.json',d)
            print(f'  LUT {d["baseline"]["metrics"]["luts"]} -> {d["winner"]["metrics"]["luts"]}; '
                  f'gain {d["gain_percent"]:.2f}%; calls {d["search_calls"]}; '
                  f'time {d["search_seconds"]:.3f}s; verified={d["verified"]}',flush=True)
            if not d['valid_result']:
                failures.append({'trial':name,'reason':'unverified or infeasible result'})
        except Exception as exc:
            failures.append({'trial':name,'reason':repr(exc)})
            print('  FAILED:',repr(exc),flush=True)
        summarize_csv(output)
    rows=summarize_csv(output)
    completion={'completed_utc':datetime.now(timezone.utc).isoformat(),'planned_trials':len(jobs),
                'completed_trials':len(rows),'failures':failures,
                'all_valid':len(rows)==len(jobs) and all(r['valid_result'] for r in rows) and not failures}
    write_json(output/'completion.json',completion)
    return completion
