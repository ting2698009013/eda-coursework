"""Rebuild and formally verify the illustrative AIG/LUT ranking counterexample."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lutsearch.abc import ABC


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    analysis = json.loads((run/'report/analysis.json').read_text(encoding='utf-8'))
    example = next((d for d in analysis['candidate_evidence'] if d['counterexample']), None)
    if example is None:
        print('No counterexample observed; nothing to replay.')
        return
    out = run/'counterexample'
    out.mkdir(exist_ok=False)
    abc = ABC(); records = []
    with tempfile.TemporaryDirectory(prefix='lut-counterexample-') as td:
        td = Path(td)
        for label, record in example['counterexample'].items():
            source = run/'trials'/record['trial']/'input.aig'
            candidate = abc.evaluate(source, record['sequence'], td/label)
            checks = {}
            destination = out/label
            shutil.copytree(td/label, destination)
            for suffix, file in [('aig', candidate.aig), ('blif', candidate.blif)]:
                check = abc.verify(source, file, td/(label+'_'+suffix))
                (destination/('cec_'+suffix+'.log')).write_text(check.pop('log'), encoding='utf-8')
                checks[suffix] = check['status']
            records.append({'label': label, 'metrics': asdict(candidate.metrics),
                'metrics_match': asdict(candidate.metrics) == record['metrics'],
                'structure_match': candidate.fingerprint == record['structure_sha256'],
                'verification': checks})
    a, b = records
    # JSON insertion order follows smaller_aig, fewer_luts in analyze_results.py.
    ranking_inversion = a['metrics']['and_nodes'] < b['metrics']['and_nodes'] and a['metrics']['luts'] > b['metrics']['luts']
    valid = ranking_inversion and all(d['metrics_match'] and d['structure_match']
            and all(v == 'equivalent' for v in d['verification'].values()) for d in records)
    result = {'circuit': example['circuit'], 'passed': valid, 'checks': records}
    (out/'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if valid else 2)


if __name__ == '__main__':
    main()
