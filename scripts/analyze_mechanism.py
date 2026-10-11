"""Describe the prespecified length and mapped-all scoring controls; no selective testing."""
import argparse
import collections
import json
import math
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lutsearch.search import write_json

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def entries(p):return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()]
def mean_available(values):
    values=[v for v in values if v is not None]
    return statistics.mean(values) if values else None

def analyze(base):
    length=base/'e3_length';score=base/'e3_score'
    for path in (length,score):
        if read(path/'phase.json')['phase']!='complete':raise ValueError('Mechanism stages not fully validated')
    trials=[];groups=collections.defaultdict(list)
    for f in sorted(length.glob('trials/*/summary.json')):
        d=read(f);trace=entries(f.parent/'trace.jsonl');mapped=[t for t in trace if t['status']=='ok' and t.get('metrics',{}).get('luts') is not None]
        feasible=[t for t in mapped if t['metrics']['lut_levels']<=d['depth_limit']]
        gains=[t['elapsed_seconds'] for t in trace if t['incumbent_luts']<d['baseline']['metrics']['luts']]
        row=dict(circuit=d['circuit'],method=d['method'],replicate_id=d['replicate_id'],seed=d['seed'],
            luts=d['winner']['metrics']['luts'],levels=d['winner']['metrics']['lut_levels'],
            baseline_luts=d['baseline']['metrics']['luts'],calls=d['search_calls'],mapped=len(mapped),
            feasible=len(feasible),feasible_fraction=len(feasible)/len(mapped) if mapped else None,
            unique_structures=len({t['structure_sha256'] for t in mapped}),
            mean_command_seconds=statistics.mean(t['seconds'] for t in mapped) if mapped else None,
            search_seconds=d['search_seconds'],first_lut_gain_seconds=gains[0] if gains else None,
            stop=d['stop_reason'],cache_hits=d['cache_hits'])
        trials.append(row);groups[(d['circuit'],d['method'])].append(row)
    aggregate=[]
    for (c,m),rs in sorted(groups.items()):
        aggregate.append(dict(circuit=c,method=m,n=len(rs),median_luts=statistics.median(r['luts'] for r in rs),
            mean_log_luts=statistics.mean(math.log(r['luts']) for r in rs),
            mean_calls=statistics.mean(r['calls'] for r in rs),mean_mapped=statistics.mean(r['mapped'] for r in rs),
            mean_feasible_fraction=mean_available(r['feasible_fraction'] for r in rs),
            mean_unique_structures=statistics.mean(r['unique_structures'] for r in rs),
            mean_command_seconds=mean_available(r['mean_command_seconds'] for r in rs),
            mean_search_seconds=statistics.mean(r['search_seconds'] for r in rs),stops=dict(collections.Counter(r['stop'] for r in rs))))
    lookup={(r['circuit'],r['method']):r for r in aggregate};circuits=sorted({r['circuit'] for r in aggregate})
    contrasts={c:100*(1-math.exp(lookup[(c,'random_len10')]['mean_log_luts']-lookup[(c,'random_len3')]['mean_log_luts'])) for c in circuits}
    pooled=100*(1-math.exp(statistics.mean(lookup[(c,'random_len10')]['mean_log_luts']-lookup[(c,'random_len3')]['mean_log_luts'] for c in circuits)))
    score_rows=[];pruning=[]
    for f in sorted(score.glob('trials/*/summary.json')):
        d=read(f);trace={t['call']:t for t in entries(f.parent/'trace.jsonl')}
        selected=entries(f.parent/'selection.jsonl');misses=0
        for s in selected:
            pool=[trace[i] for i in s['pool_calls'] if trace[i]['metrics']['lut_levels']<=d['depth_limit']]
            kept=[trace[i] for i in s['selected_calls'] if trace[i]['metrics']['lut_levels']<=d['depth_limit']]
            key=lambda t:(t['metrics']['luts'],t['metrics']['lut_levels'])
            pool_best=min(pool,key=key) if pool else None;kept_best=min(kept,key=key) if kept else None
            missed=pool_best is not None and (kept_best is None or key(kept_best)>key(pool_best))
            misses+=missed
            pruning.append(dict(circuit=d['circuit'],method=d['method'],depth=s['depth'],
                feasible_pool=len(pool),feasible_kept=len(kept),pruned_all_best_feasible=missed,
                pool_best=pool_best,kept_best=kept_best))
        score_rows.append(dict(circuit=d['circuit'],method=d['method'],luts=d['winner']['metrics']['luts'],
            levels=d['winner']['metrics']['lut_levels'],sequence=d['winner']['sequence'],calls=d['search_calls'],
            search_seconds=d['search_seconds'],stop=d['stop_reason'],max_depth=d['max_evaluated_depth'],
            layers_pruning_all_best_feasible=misses))
    result=dict(length_trials=trials,length_aggregate=aggregate,length10_vs3_percent=contrasts,
        length10_vs3_equal_case_percent=pooled,score_results=score_rows,pruning_events=pruning,
        limits=['Three purposefully selected diagnostic cases; no population-level inference.',
            'Length changes evaluation cost, attainable structures and finite search-space size together.',
            'Mapped-all score control updates the incumbent using every evaluated candidate; only retention uses AIG or LUT ranking.',
            'Pruning a candidate removes its future descendants, not an already discovered best output.'])
    write_json(base/'mechanism_analysis.json',result)
    print(json.dumps({k:result[k] for k in ['length10_vs3_percent','length10_vs3_equal_case_percent','score_results']},ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('base',type=Path);a=p.parse_args();analyze(a.base)
