"""Sequential randomized time blocks, method-specific repeats and incremental records."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import csv
import json
import platform
import random
import shutil
import sys
import time
import zipfile
from .abc import ABC, ROOT, sha256
from .experiment import source_hash, complete_trial, preserve_interrupted, summarize_csv
from .search import SearchConfig, build_reference, run_search, write_json

def utc():return datetime.now(timezone.utc).isoformat()

def make_jobs(spec):
    rng=random.Random(spec.get('scheduling_seed',20261009));blocks=[]
    labels=[m.get('label',m['method']) for m in spec['method_runs']]
    if len(labels)!=len(set(labels)):raise ValueError('Method labels must be unique')
    for c in spec['circuits']:
        for p in spec['protocols']:
            for r in range(max(m['repeats'] for m in spec['method_runs'])):
                block=[]
                for m in spec['method_runs']:
                    if r>=m['repeats']:continue
                    label=m.get('label',m['method'])
                    seed=m['seed_start']+r if 'seed_start' in m else 0
                    block.append(dict(circuit=c,protocol=p,method=m['method'],label=label,seed=seed,
                        replicate_id=r,block_id=f'{c}__{p["name"]}__r{r}',overrides=m.get('config',{}),
                        trial=f'{c}__{p["name"]}__{label}__r{r}'))
                rng.shuffle(block);blocks.append(block)
    rng.shuffle(blocks)
    return [j for block in blocks for j in block]

def run_experiment(config_path:Path,output:Path,abc:ABC,resume=False):
    session_start=time.perf_counter()
    spec=json.loads(config_path.read_text(encoding='utf-8'))
    if 'method_runs' not in spec:
        from .experiment import run_experiment as legacy
        return legacy(config_path,output,abc,resume)
    manifest_path=ROOT/spec.get('manifest','manifest.json')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    circuits={c['name']:c for c in manifest['circuits']}
    selected=[circuits[n] for n in spec['circuits']];jobs=make_jobs(spec)
    for j in jobs:SearchConfig(method=j['method'],**j['overrides']).validate()
    memory=spec.get('memory_limit_bytes')
    if memory and not shutil.which('prlimit'):raise ValueError('prlimit unavailable')
    abc.memory_limit_bytes=memory;abc.timeout=spec.get('reference_command_timeout',30)
    identity=dict(spec=spec,abc_sha256=sha256(abc.binary),source_sha256=source_hash(),
                  manifest_sha256=sha256(manifest_path),lut_size=abc.lut_size,memory_limit_bytes=memory)
    if output.exists():
        if not resume:raise FileExistsError('Use a new directory or --resume with unchanged identity')
        metadata=json.loads((output/'experiment.json').read_text(encoding='utf-8'))
        if metadata['identity']!=identity:raise ValueError('Cannot resume: config, code, tool or manifest changed')
        if json.loads((output/'schedule.json').read_text())!=jobs:raise ValueError('Schedule mismatch')
    else:
        output.mkdir(parents=True)
        metadata=dict(identity=identity,started_utc=utc(),python=sys.version,platform=platform.platform(),
            abc_version=abc.version(),benchmark_commit=manifest['benchmark_commit'],
            methodology='Sequential ABC, randomized contiguous within-circuit time blocks; fixed sample sizes.')
        write_json(output/'experiment.json',metadata)
        write_json(output/'protocol.json',spec);write_json(output/'schedule.json',jobs)
        with zipfile.ZipFile(output/'source_snapshot.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for source in sorted((ROOT/'lutsearch').glob('*.py')):archive.write(source,'lutsearch/'+source.name)
            for source in sorted((ROOT/'scripts').glob('*.py')):archive.write(source,'scripts/'+source.name)
            archive.write(config_path,'experiment_config.json')
            archive.write(ROOT/'manifest.json','manifest.json')
            if manifest_path.resolve() != (ROOT/'manifest.json').resolve():
                archive.write(manifest_path,manifest_path.relative_to(ROOT).as_posix())
    for c in selected:
        source=ROOT/c['file']
        if sha256(source)!=c['sha256']:raise ValueError('Benchmark checksum changed: '+c['name'])
        ref=output/'references'/c['name']
        if not (ref/'reference.json').is_file():
            if ref.exists():preserve_interrupted(ref,output)
            print('REFERENCE',c['name'],flush=True)
            build_reference(abc,source,ref,spec.get('cec_timeout',30))
    recorded={p.parent.name:json.loads(p.read_text(encoding='utf-8'))
              for p in output.glob('trials/*/summary.json') if complete_trial(p)}
    failures={p.parent.name:json.loads(p.read_text(encoding='utf-8')) for p in output.glob('trials/*/failure.json')}
    if resume:
        # Preserve temporal pairing: a block interrupted halfway is rerun in full.
        blocks={}
        for j in jobs:blocks.setdefault(j['block_id'],[]).append(j['trial'])
        for names in blocks.values():
            done=[n for n in names if n in recorded or n in failures]
            if done and len(done)<len(names):
                for name in names:
                    path=output/'trials'/name
                    if path.exists():preserve_interrupted(path,output)
                    recorded.pop(name,None);failures.pop(name,None)
    for index,j in enumerate(jobs,1):
        trial=output/'trials'/j['trial'];c=circuits[j['circuit']]
        if resume and (j['trial'] in recorded or j['trial'] in failures):continue
        if trial.exists():preserve_interrupted(trial,output)
        cfg_args=dict(method=j['method'],seed=j['seed'],max_calls=j['protocol']['max_calls'],
            seconds=j['protocol'].get('seconds'),max_depth=spec.get('max_depth',10),
            beam_width=spec.get('beam_width',4),command_timeout=spec.get('command_timeout',15),
            cec_timeout=spec.get('cec_timeout',30))
        cfg_args.update(j['overrides']);cfg=SearchConfig(**cfg_args)
        started=utc();start=time.perf_counter()
        print(f'[{index}/{len(jobs)}] {j["trial"]}',flush=True)
        try:
            d=run_search(abc,ROOT/c['file'],output/'references'/c['name'],trial,cfg)
            d.update(circuit=c['name'],split=spec.get('split',c['split']),protocol=j['protocol']['name'],
                method=j['label'],algorithm_method=j['method'],replicate_id=j['replicate_id'],block_id=j['block_id'],
                started_utc=started,ended_utc=utc(),schedule_index=index,wall_seconds=time.perf_counter()-start)
            write_json(trial/'summary.json',d);recorded[j['trial']]=d
            print(f'  LUT {d["baseline"]["metrics"]["luts"]}->{d["winner"]["metrics"]["luts"]}; '
                  f'calls={d["search_calls"]}; time={d["search_seconds"]:.3f}s; valid={d["valid_result"]}',flush=True)
        except Exception as exc:
            failure=dict(trial=j['trial'],reason=repr(exc),job=j,started_utc=started,ended_utc=utc())
            trial.mkdir(parents=True,exist_ok=True);write_json(trial/'failure.json',failure)
            failures[j['trial']]=failure;print('FAILED',repr(exc),flush=True)
        with (output/'events.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps(dict(index=index,trial=j['trial'],completed_utc=utc(),wall_seconds=time.perf_counter()-start))+'\n')
        write_json(output/'progress.json',dict(planned=len(jobs),completed=len(recorded),failed=len(failures),
            last_trial=j['trial'],updated_utc=utc(),session_seconds=time.perf_counter()-session_start))
    # Read all summaries only once at completion, rather than after every trial (quadratic I/O).
    summarize_csv(output)
    invalid=[name for name,d in recorded.items() if not d['valid_result']]
    completion=dict(completed_utc=utc(),planned_trials=len(jobs),completed_trials=len(recorded),
        failures=list(failures.values()),invalid_trials=invalid,session_seconds=time.perf_counter()-session_start,
        wall_seconds=(datetime.now(timezone.utc)-datetime.fromisoformat(metadata['started_utc'])).total_seconds(),
        all_valid=len(recorded)==len(jobs) and not invalid and not failures)
    write_json(output/'completion.json',completion)
    return completion
