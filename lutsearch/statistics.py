"""Prespecified equal-circuit log ratio, paired time-block bootstrap (standard library)."""
import math
import random
import statistics

def percentile(values,p):
    ordered=sorted(values);x=(len(ordered)-1)*p;i=int(x);fraction=x-i
    return ordered[i]*(1-fraction)+ordered[min(i+1,len(ordered)-1)]*fraction

def paired_comparison(records,candidate='lut_beam',reference='random',iterations=10000,seed=314159):
    relevant=[d for d in records if d['method'] in (candidate,reference)]
    if not relevant or any(not d['valid_result'] for d in relevant):
        return {'status':'blocked','reason':'Missing or invalid primary results; no silent complete-case filtering'}
    cells={}
    for d in relevant:
        key=(d['circuit'],d['block_id'])
        if d['method'] in cells.setdefault(key,{}):raise ValueError('Duplicate block/method result')
        cells[key][d['method']]=d
    groups={}
    for (c,b),pair in cells.items():
        if set(pair)!={candidate,reference}:return {'status':'blocked','reason':'Incomplete paired block'}
        a=pair[candidate]['winner']['metrics']['luts'];r=pair[reference]['winner']['metrics']['luts']
        if min(a,r)<=0:raise ValueError('Positive LUT counts required for log ratio')
        groups.setdefault(c,[]).append(math.log(a/r))
    values=list(groups.values());means=[statistics.mean(v) for v in values]
    convert=lambda x:100*(1-math.exp(x))
    rng=random.Random(seed);draws=[];circuit_draws=[]
    for _ in range(iterations):
        draws.append(convert(statistics.mean(statistics.mean(rng.choices(v,k=len(v))) for v in values)))
        circuit_draws.append(convert(statistics.mean(rng.choices(means,k=len(means)))))
    return dict(status='ok',candidate=candidate,reference=reference,delta_percent=convert(statistics.mean(means)),
        ci95_percent=[percentile(draws,.025),percentile(draws,.975)],
        circuit_composition_sensitivity95=[percentile(circuit_draws,.025),percentile(circuit_draws,.975)],
        circuits=len(groups),blocks_per_circuit={c:len(v) for c,v in groups.items()},iterations=iterations,seed=seed,
        interpretation='Positive means candidate uses fewer LUTs. Primary interval conditions on this circuit set; circuit sensitivity is not universal generalization.',
        per_circuit={c:dict(delta_percent=convert(statistics.mean(v)),wins=sum(x<0 for x in v),
                            ties=sum(x==0 for x in v),losses=sum(x>0 for x in v)) for c,v in groups.items()})
