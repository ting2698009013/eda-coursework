"""Search strategies with an explicit shared budget and immutable AIG candidates."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import random
import shutil
import subprocess
import tempfile
import time
from .abc import ABC, Candidate, Metrics, OPS, RESYN2, ToolError, sha256, validate_aiger

METHODS = ('random', 'greedy', 'aig_beam', 'lut_beam', 'lut_beam_diverse', 'aig_beam_mapped', 'sequence_local')

@dataclass
class SearchConfig:
    method: str = 'lut_beam_diverse'
    seed: int = 0
    max_calls: int = 120
    seconds: float | None = None
    max_depth: int = 10
    beam_width: int = 4
    command_timeout: float = 15.0
    cec_timeout: float = 30.0
    random_length: int | None = None
    restart_after: int = 10

    def validate(self):
        if self.method not in METHODS:
            raise ValueError(f'Unknown method {self.method}')
        if self.max_calls < 1 or not 1 <= self.max_depth <= 10 or self.beam_width < 1:
            raise ValueError('Positive budgets, depth 1..10 and positive beam width required')
        if self.seconds is not None and self.seconds <= 0:
            raise ValueError('Time budget must be positive')
        if self.command_timeout <= 0 or self.cec_timeout <= 0:
            raise ValueError('Timeout must be positive')
        if self.random_length is not None and not 1 <= self.random_length <= self.max_depth:
            raise ValueError('Random sequence length must be within the depth limit')
        if self.restart_after < 1:
            raise ValueError('Restart threshold must be positive')
        if self.method == 'sequence_local' and self.max_depth != len(RESYN2):
            raise ValueError('Complete-sequence local search requires length 10')

def objective(m: Metrics, depth_limit: int):
    """Feasibility first, then LUT count, then levels. No scalar trade-off hides violations."""
    if m.luts is None or m.lut_levels is None:
        raise ValueError('Mapped metrics required')
    return (m.lut_levels > depth_limit, max(0, m.lut_levels-depth_limit), m.luts, m.lut_levels)

def select_beam(pool: list[Candidate], width: int, limit: int, mode: str, rng: random.Random):
    """Deduplicate AIG structural payloads, never by function or node counts."""
    unique = {}
    for c in sorted(pool, key=lambda c: c.sequence):
        unique.setdefault(c.fingerprint, c)
    if mode in ('aig_beam', 'aig_beam_mapped'):
        key = lambda c: (c.metrics.and_nodes, c.metrics.aig_levels, c.sequence)
    else:
        key = lambda c: (*objective(c.metrics, limit), c.metrics.and_nodes, c.sequence)
    ordered = sorted(unique.values(), key=key)
    if mode == 'lut_beam_diverse' and width > 1 and len(ordered) > width:
        # One exploration slot helps avoid pruning a temporarily worse intermediate graph.
        return ordered[:width-1] + [rng.choice(ordered[width-1:])]
    return ordered[:width]

def candidate_json(c):
    return {'sequence': list(c.sequence), 'metrics': asdict(c.metrics),
            'aig_sha256': sha256(c.aig), 'structure_sha256': c.fingerprint, 'seconds': c.seconds}

def write_json(path, obj):
    path = Path(path)
    temporary = path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)

def build_reference(abc: ABC, source: Path, output: Path, cec_timeout=30):
    """Prepare and verify each reference in turn, retaining evidence on partial failure."""
    validate_aiger(source)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='lutref-') as temp:
        temp = Path(temp)
        local = temp / 'original.aig'
        shutil.copyfile(source, local)
        records = {}
        info = {'input_sha256': sha256(source), 'setup_seconds': 0.0,
                'baseline_cec_seconds': 0.0, 'records': records}
        for name, sequence in [('unoptimized', ()), ('resyn2', RESYN2), ('resyn2x2', RESYN2 + RESYN2)]:
            dest = output / name
            dest.mkdir()
            work = temp / name
            write_json(output / 'reference_partial.json', {**info, 'complete': False, 'current': name, 'state': 'evaluating'})
            try:
                start = time.perf_counter()
                try:
                    c = abc.evaluate(local, sequence, work)
                finally:
                    info['setup_seconds'] += time.perf_counter() - start
                shutil.copyfile(c.aig, dest / 'candidate.aig')
                shutil.copyfile(c.blif, dest / 'candidate.blif')
                (dest / 'abc.log').write_text(c.log, encoding='utf-8')
                records[name] = candidate_json(c)
                write_json(output / 'reference_partial.json', {**info, 'complete': False, 'current': name, 'state': 'verifying'})
                for suffix, key, target in [('blif', 'verification', c.blif), ('aig', 'aig_verification', c.aig)]:
                    check = abc.verify(local, target, temp / ('verify_' + name + '_' + suffix), cec_timeout)
                    logfile = 'cec.log' if suffix == 'blif' else 'cec_aig.log'
                    (dest / logfile).write_text(check.pop('log'), encoding='utf-8')
                    records[name][key] = check
                    info['baseline_cec_seconds'] += check['seconds']
                    write_json(output / 'reference_partial.json', {**info, 'complete': False, 'current': name, 'state': 'verifying'})
                if any(records[name][k]['status'] != 'equivalent' for k in ('verification', 'aig_verification')):
                    raise ToolError(f'Reference {name} did not pass both CEC checks: {records[name]["verification"]["status"]}, {records[name]["aig_verification"]["status"]}')
            except Exception as exc:
                for filename in ('abc.log', 'commands.abc', 'candidate.aig', 'candidate.blif'):
                    if (work / filename).exists():shutil.copyfile(work / filename, dest / filename)
                write_json(output / 'reference_partial.json', {**info, 'complete': False, 'current': name, 'state': 'failed', 'error': repr(exc)})
                raise
        write_json(output / 'reference_partial.json', {**info, 'complete': True, 'state': 'complete'})
        write_json(output / 'reference.json', info)
    return info

def load_reference(directory: Path):
    info = json.loads((directory / 'reference.json').read_text(encoding='utf-8'))
    def get(name):
        d = info['records'][name]
        p = directory / name
        return Candidate(tuple(d['sequence']), Metrics(**d['metrics']), p / 'candidate.aig',
                         p / 'candidate.blif', d['structure_sha256'], d['seconds'], '')
    return info, get('unoptimized'), get('resyn2')

def run_search(abc: ABC, source: Path, reference_dir: Path, output: Path, cfg: SearchConfig):
    cfg.validate()
    validate_aiger(source)
    ref, root, baseline = load_reference(reference_dir)
    if ref['input_sha256'] != sha256(source):
        raise ValueError('Reference belongs to a different input')
    if output.exists():
        raise FileExistsError('Refusing to overwrite a previous run: ' + str(output))
    output.mkdir(parents=True)
    write_json(output / 'config.json', asdict(cfg))
    rng = random.Random(cfg.seed)
    depth_limit = baseline.metrics.lut_levels
    best = baseline
    trace, raw_logs = [], []
    selections = []
    local_steps = []
    structure_duplicates = 0
    calls = 0
    errors = 0
    cache_hits = 0
    map_calls = 0
    seen = set()
    stop_reason = 'completed_tree'
    timeout_end = False
    total_start = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix='lutsearch-') as td:
        temp = Path(td)
        local = temp / 'input.aig'
        shutil.copyfile(source, local)
        # Start timing after immutable input staging; all candidate execution/selection is included.
        search_start = time.perf_counter()

        def available():
            return calls < cfg.max_calls and (cfg.seconds is None or time.perf_counter()-search_start < cfg.seconds) and not timeout_end

        def assess(sequence, parent=None, lineage=None):
            nonlocal calls, errors, cache_hits, best, timeout_end, map_calls
            if not available():
                return None
            kind = 'mapping' if parent is not None else ('aig_only' if cfg.method == 'aig_beam' else 'full')
            cache_key = (kind, tuple(sequence))
            if cache_key in seen:
                cache_hits += 1
                return None
            seen.add(cache_key)
            calls += 1
            command_dir = temp / f'call-{calls:05d}'
            remaining = cfg.command_timeout
            if cfg.seconds is not None:
                remaining = min(remaining, max(0.001, cfg.seconds-(time.perf_counter()-search_start)))
            entry = {'call': calls, 'kind': kind, 'sequence': list(sequence),
                     'depth': len(sequence), 'parent_id': (parent or lineage).ident if (parent or lineage) is not None else None}
            c = None
            try:
                if parent is not None:
                    c = abc.map_candidate(parent, command_dir, remaining)
                else:
                    c = abc.evaluate(local, sequence, command_dir, mapped=kind=='full', timeout=remaining)
                c.ident = calls
                if c.metrics.luts is not None:
                    map_calls += 1
                    if objective(c.metrics, depth_limit) < objective(best.metrics, depth_limit):
                        best = c
                entry.update({'status':'ok', **candidate_json(c)})
            except subprocess.TimeoutExpired:
                entry['status'] = 'timeout'
                errors += 1
                # A deadline-capped command may exceed the wall limit slightly while being reaped.
                if cfg.seconds is not None and time.perf_counter()-search_start >= cfg.seconds:
                    timeout_end = True
            except ToolError as exc:
                entry.update(status='tool_error', error=str(exc))
                errors += 1
            log_path = command_dir / 'abc.log'
            raw_logs.append(f'\n===== SEARCH CALL {calls} ({kind}) =====\n' +
                            (log_path.read_text(encoding='utf-8') if log_path.exists() else entry.get('error','')))
            entry.update(elapsed_seconds=time.perf_counter()-search_start,
                         incumbent_luts=best.metrics.luts, incumbent_levels=best.metrics.lut_levels)
            trace.append(entry)
            return c

        if cfg.method == 'random':
            attempts = 0
            space_size = len(OPS)**(cfg.random_length or cfg.max_depth)
            while available() and attempts < cfg.max_calls*100 and len(seen) < space_size:
                attempts += 1
                seq = tuple(rng.choice(OPS) for _ in range(cfg.random_length or cfg.max_depth))
                assess(seq)
            stop_reason = 'completed_space' if len(seen) == space_size else 'attempt_limit'
        elif cfg.method == 'sequence_local':
            # Initialization is assessed again inside the budget, rather than giving a free extra candidate.
            initial = assess(RESYN2)
            current = initial or baseline
            local_steps.append({'action':'initialize', 'sequence':list(RESYN2),
                'candidate_call':initial.ident if initial is not None else None,
                'status':'ok' if initial is not None else trace[-1]['status'] if trace else 'no_budget'})
            stagnant = 0
            attempts = 0
            while available() and attempts < cfg.max_calls*100:
                attempts += 1
                restarting = stagnant >= cfg.restart_after
                before = current
                if restarting:
                    sequence = tuple(rng.choice(OPS) for _ in range(cfg.max_depth))
                else:
                    position = rng.randrange(cfg.max_depth)
                    sequence = list(current.sequence)
                    sequence[position] = rng.choice([op for op in OPS if op != sequence[position]])
                    sequence = tuple(sequence)
                prior_calls = calls
                child = assess(sequence, lineage=None if restarting else current)
                strict = child is not None and objective(child.metrics, depth_limit) < objective(current.metrics, depth_limit)
                accepted = child is not None and (restarting or objective(child.metrics, depth_limit) <= objective(current.metrics, depth_limit))
                old_stagnant = stagnant
                if accepted:current = child
                stagnant = 0 if (restarting and child is not None) or strict else stagnant+1
                local_steps.append({'action':'restart' if restarting else 'mutate',
                    'before_sequence':list(before.sequence), 'sequence':list(sequence),
                    'parent_call':None if restarting else before.ident,
                    'candidate_call':calls if calls > prior_calls else None,
                    'status':trace[-1]['status'] if calls > prior_calls else 'cached',
                    'accepted':accepted, 'strict_improvement':strict,
                    'stagnation_before':old_stagnant, 'stagnation_after':stagnant})
            stop_reason = 'attempt_limit'
        else:
            beam = [root]
            width = 1 if cfg.method == 'greedy' else cfg.beam_width
            for _depth in range(1, cfg.max_depth+1):
                pool = []
                for parent in beam:
                    for op in OPS:
                        child = assess(parent.sequence + (op,), lineage=parent)
                        if child is not None:
                            pool.append(child)
                        if not available():
                            break
                    if not available():
                        break
                if not pool:
                    stop_reason = 'no_expandable_candidates'
                    break
                beam = select_beam(pool, width, depth_limit, cfg.method, rng)
                structure_duplicates += len(pool)-len({c.fingerprint for c in pool})
                chosen = {c.ident for c in beam}
                representatives = {}
                for c in sorted(pool, key=lambda x:x.sequence):
                    representatives.setdefault(c.fingerprint, c.ident)
                exploration = beam[-1].ident if cfg.method == 'lut_beam_diverse' and len(representatives)>width and width>1 else None
                selections.append({'depth': _depth, 'pool_calls': [c.ident for c in pool],
                    'selected_calls': [c.ident for c in beam], 'candidates': [
                    {'call': c.ident, 'retained': c.ident in chosen,
                     'reason': ('exploration' if c.ident == exploration else 'score') if c.ident in chosen else
                               ('duplicate_structure' if representatives[c.fingerprint]!=c.ident else 'pruned_score')}
                    for c in pool]})
                if cfg.method == 'aig_beam':
                    # Real AIG-only screening: only retained candidates incur LUT mapping cost.
                    # Every additional mapping invocation counts against the same call/time budget.
                    for c in beam:
                        assess(c.sequence, parent=c)
                if not available():
                    stop_reason = 'budget'
                    break
        search_seconds = time.perf_counter()-search_start
        if calls >= cfg.max_calls:
            stop_reason = 'call_budget'
        elif cfg.seconds is not None and search_seconds >= cfg.seconds:
            stop_reason = 'time_budget'

        # Export the actual selected AIG/BLIF, not a separately optimized reconstruction.
        export_start = time.perf_counter()
        shutil.copyfile(local, output / 'input.aig')
        shutil.copyfile(best.aig, output / 'winner.aig')
        shutil.copyfile(best.blif, output / 'winner.blif')
        (output / 'winner.abc').write_text('; '.join(['read_aiger input.aig', 'strash', *best.sequence,
            'write_aiger -s replay.aig', f'if -K {abc.lut_size}', 'write_blif replay.blif', 'print_stats'])+'\n', encoding='utf-8')
        export_seconds = time.perf_counter()-export_start
        checks = {}
        for suffix in ['aig','blif']:
            check = abc.verify(local, output / ('winner.'+suffix), temp / ('verify_'+suffix), cfg.cec_timeout)
            (output / ('cec_'+suffix+'.log')).write_text(check.pop('log'), encoding='utf-8')
            checks[suffix] = check
        verified = all(c['status']=='equivalent' for c in checks.values())
        summary = {'schema_version':2, 'method':cfg.method, 'seed':cfg.seed,
            'config':asdict(cfg), 'input_sha256':sha256(source), 'abc_sha256':sha256(abc.binary),
            'lut_size':abc.lut_size, 'depth_limit':depth_limit,
            'baseline':candidate_json(baseline), 'winner':candidate_json(best),
            'verified':verified, 'verification':checks,
            'valid_result':verified and best.metrics.lut_levels <= depth_limit,
            'search_calls':calls, 'mapping_evaluations':map_calls,
            'cache_hits':cache_hits, 'error_calls':errors,
            'structure_duplicates':structure_duplicates,
            'local_restarts':sum(s['action']=='restart' and s['status']=='ok' for s in local_steps),
            'local_accepted_mutations':sum(s['action']=='mutate' and s.get('accepted',False) for s in local_steps),
            'successful_evaluations':sum(t['status']=='ok' for t in trace),
            'max_evaluated_depth':max((len(t['sequence']) for t in trace if t['status']=='ok'),default=0),
            'peak_rss_kib':None, 'peak_rss_note':'Not sampled per trial; missing, not zero.',
            'export_seconds':export_seconds, 'cec_seconds':sum(c['seconds'] for c in checks.values()),
            'search_seconds':search_seconds, 'total_seconds':time.perf_counter()-total_start,
            'common_reference_setup_seconds':ref['setup_seconds'],
            'stop_reason':stop_reason, 'fallback_to_baseline':best is baseline,
            'gain_percent':100*(baseline.metrics.luts-best.metrics.luts)/max(1,baseline.metrics.luts),
            'timing_note':'Sequential ABC processes. Search time includes candidate execution, local temporary I/O and selection. Shared reference setup, final export and CEC are reported separately. Deadline includes process startup and reaping overhead.'}
        write_json(output / 'summary.json', summary)
        (output / 'trace.jsonl').write_text(''.join(json.dumps(t, ensure_ascii=False)+'\n' for t in trace), encoding='utf-8')
        (output / 'selection.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in selections), encoding='utf-8')
        if local_steps:
            (output / 'local_steps.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in local_steps), encoding='utf-8')
        (output / 'abc.log').write_text(''.join(raw_logs), encoding='utf-8')
    return summary
