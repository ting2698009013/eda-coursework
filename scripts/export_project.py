"""Bundle source, licensed benchmarks and one audited run; omit local tool binaries."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    run, output = args.run.resolve(), args.out.resolve()
    run.relative_to(ROOT/'results')
    checks = [('completion.json', 'all_valid'), ('audit.json', 'passed'), ('replay_audit.json', 'passed')]
    for name, key in checks:
        data = json.loads((run/name).read_text(encoding='utf-8'))
        if not data[key]:
            raise ValueError(f'Cannot export an unvalidated run: {name}')
    files = [ROOT/name for name in ('README.md', 'pyproject.toml', 'run.ps1', 'manifest.json', '.gitignore')]
    for directory in ('lutsearch', 'configs', 'scripts', 'docs', 'examples', 'tests', 'benchmarks'):
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    files.extend(p for p in run.rglob('*') if p.is_file() and 'interrupted' not in p.relative_to(run).parts)
    output.parent.mkdir(parents=True, exist_ok=True)
    digests = {}
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(set(files)):
            name = 'lut-search/'+file.relative_to(ROOT).as_posix()
            payload = file.read_bytes()
            archive.writestr(name, payload)
            digests[name] = hashlib.sha256(payload).hexdigest()
        archive.writestr('FILE_SHA256.json', json.dumps(digests, indent=2, ensure_ascii=False))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Archive CRC verification failed')
    print(json.dumps({'archive': str(output), 'files': len(files), 'bytes': output.stat().st_size,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
