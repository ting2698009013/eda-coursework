"""Fetch pinned official tools and EPFL inputs; extract tools locally, never install globally."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '82d8cc6910419298e713a46644ed59fd3df53038'
PACKAGES = [
    ('pool/universe/y/yosys/yosys-abc_0.33-5build2_amd64.deb', '916ba59b6ca6dd1b6b927c7364357c2801ccb85db7eb3ca858ac9a856270fa44'),
    ('pool/universe/y/yosys/yosys_0.33-5build2_amd64.deb', '94247f2157ec6ce63c6638326e419975d911ace5356b3dc276d1d8b36659efbd'),
    ('pool/main/t/tcl8.6/libtcl8.6_8.6.14+dfsg-1build1_amd64.deb', '7534eb825f6317514d747ce53b4d6c23bf24df1dc1785f9599610ece5515e685'),
]
# Defined before any optimization experiments. Two development circuits, ten held-out tests.
CIRCUITS = [
    ('ctrl', 'random_control', 'development'), ('int2float', 'random_control', 'development'),
    ('adder', 'arithmetic', 'test'), ('bar', 'arithmetic', 'test'),
    ('max', 'arithmetic', 'test'), ('sin', 'arithmetic', 'test'),
    ('arbiter', 'random_control', 'test'), ('cavlc', 'random_control', 'test'),
    ('dec', 'random_control', 'test'), ('i2c', 'random_control', 'test'),
    ('priority', 'random_control', 'test'), ('router', 'random_control', 'test'),
]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def fetch(url, path, expected=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        print('Downloading', path.name, flush=True)
        req = urllib.request.Request(url, headers={'User-Agent': 'EDA-Course-LUT-Search/1.0'})
        with urllib.request.urlopen(req, timeout=90) as response:
            payload = response.read()
        if expected and hashlib.sha256(payload).hexdigest() != expected:
            raise RuntimeError('Checksum mismatch: ' + path.name)
        path.write_bytes(payload)
    digest = sha(path)
    if expected and digest != expected:
        raise RuntimeError('Existing file has wrong checksum: ' + str(path))
    return {'url': url, 'sha256': digest, 'bytes': path.stat().st_size}

def linux_path(p):
    if os.name == 'nt':
        return '/mnt/' + p.drive[0].lower() + p.as_posix()[2:]
    return str(p)

def main():
    # Treat the checked-in manifest as a lockfile; do not silently bless a changed
    # local circuit by overwriting its expected digest during bootstrap.
    pinned = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    if pinned['benchmark_commit'] != COMMIT:
        raise RuntimeError('Benchmark commit differs from the checked-in manifest')
    pinned_inputs = {item['name']: item for item in pinned['circuits']}
    tool_dir = ROOT / '.tools'
    tool_dir.mkdir(exist_ok=True)
    tools = []
    for relative, digest in PACKAGES:
        name = relative.rsplit('/', 1)[1]
        archive = tool_dir / 'downloads' / name
        info = fetch('https://archive.ubuntu.com/ubuntu/' + relative, archive, digest)
        cmd = ['dpkg-deb', '-x', linux_path(archive), linux_path(tool_dir)]
        if os.name == 'nt':
            cmd = ['wsl', '-d', 'Ubuntu', '--exec'] + cmd
        subprocess.run(cmd, check=True)
        tools.append({'package': name, **info})
    data = []
    for name, group, split in CIRCUITS:
        rel = f'{group}/{name}.aig'
        info = fetch(f'https://raw.githubusercontent.com/lsils/benchmarks/{COMMIT}/{rel}',
                     ROOT / 'benchmarks' / f'{name}.aig', pinned_inputs[name]['sha256'])
        data.append({'name': name, 'group': group, 'split': split, 'file': f'benchmarks/{name}.aig', **info})
    for name in ['LICENSE', 'README.md']:
        expected = ('83e4dd21429a91fb7cea67a476032a9641425e5355df2e0f589a738b6ec9fd2c'
                    if name == 'LICENSE' else None)
        fetch(f'https://raw.githubusercontent.com/lsils/benchmarks/{COMMIT}/{name}',
              ROOT / 'benchmarks' / ('EPFL_' + name), expected)
    manifest = {'benchmark_commit': COMMIT, 'circuits': data, 'tools': tools,
                'tool_platform': 'Ubuntu 24.04 amd64 (WSL on Windows)',
                'package_checksum_source': 'Ubuntu noble apt package metadata',
                'selection': 'Preselected: two development and ten held-out test circuits. No circuits selected by optimization outcome.'}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Ready:', ROOT, flush=True)

if __name__ == '__main__':
    main()
