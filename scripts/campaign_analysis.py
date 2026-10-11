"""Read-only stage analysis. Never drops invalid or absent primary runs silently."""
import argparse
import collections
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lutsearch.statistics import paired_comparison,percentile
from lutsearch.search import write_json

def analyze(run):
    spec=json.loads((run/'protocol.json').read_text(encoding='utf-8'))
    completion=json.loads((run/'completion.json').read_text(encoding='utf-8'))
    validation_passed=completion['all_valid']
    for filename in ('audit.json','replay_audit.json'):
        path=run/filename
        validation_passed=validation_passed and path.exists() and json.loads(path.read_text(encoding='utf-8')).get('passed',False)
    # Canonical order makes seeded resampling independent of filesystem enumeration.
    records=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(run.glob('trials/*/summary.json'))]
    groups=collections.defaultdict(list)
    for d in records:groups[(d['protocol'],d['circuit'],d['method'])].append(d)
    descriptive=[]
    for (p,c,m),ds in sorted(groups.items()):
        vals=[d['winner']['metrics']['luts'] for d in ds]
        descriptive.append(dict(protocol=p,circuit=c,method=m,n=len(vals),mean=statistics.mean(vals),
            median=statistics.median(vals),q25=percentile(vals,.25),q75=percentile(vals,.75),min=min(vals),max=max(vals),
            baseline=ds[0]['baseline']['metrics']['luts'],valid=sum(d['valid_result'] for d in ds),
            median_lut_levels=statistics.median(d['winner']['metrics']['lut_levels'] for d in ds),
            depth_limit=ds[0]['depth_limit'],
            fallback_rate=statistics.mean(d['fallback_to_baseline'] for d in ds),
            mean_depth=statistics.mean(d['max_evaluated_depth'] for d in ds),
            mean_calls=statistics.mean(d['search_calls'] for d in ds),
            mean_success_per_second=statistics.mean(d['successful_evaluations']/max(d['search_seconds'],1e-9) for d in ds),
            mean_search_seconds=statistics.mean(d['search_seconds'] for d in ds),
            stops=dict(collections.Counter(d['stop_reason'] for d in ds))))
    comparisons={}
    for p in spec['protocols']:
        ds=[d for d in records if d['protocol']==p['name']]
        comparisons[p['name']]=({'status':'descriptive_only','reason':'Mechanism/development stage; no confirmatory interval'} if spec.get('descriptive_only') else
            (paired_comparison(ds,spec.get('primary_method','lut_beam'),spec.get('reference_method','random'))
            if validation_passed else {'status':'blocked','reason':'Stage incomplete or failed initial validation, audit or replay; no unconditional primary interval'})
            )
    result=dict(stage=spec.get('stage'),completion=completion,primary=comparisons,descriptive=descriptive)
    write_json(run/'analysis.json',result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run',type=Path);a=p.parse_args()
    result=analyze(a.run);print(json.dumps(result['primary'],ensure_ascii=False,indent=2))
