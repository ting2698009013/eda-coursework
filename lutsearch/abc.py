"""ABC process boundary. Every metric comes from a saved raw tool log."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import atexit
import time

ROOT = Path(__file__).resolve().parents[1]
OPS = ('balance', 'rewrite', 'rewrite -z', 'refactor', 'refactor -z')
RESYN2 = ('balance', 'rewrite', 'refactor', 'balance', 'rewrite', 'rewrite -z',
          'balance', 'refactor -z', 'rewrite -z', 'balance')
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

class ToolError(RuntimeError):
    pass

def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def structure_sha256(p: Path) -> str:
    """Hash AIGER's circuit payload, excluding symbols and timestamp comments.

    Binary AND gates contain two unsigned varints each. Consume those fields
    explicitly: searching for a comment delimiter inside binary data is unsafe.
    This is conservative structural equality, not graph-isomorphism testing.
    """
    data = p.read_bytes()
    end = data.find(b'\n') + 1
    if not end:
        raise ToolError('Truncated AIGER header')
    try:
        header = data[:end].decode('ascii').split()
        if len(header) < 6 or header[0] not in ('aig', 'aag'):
            raise ValueError('invalid header')
        maximum, inputs, latches, outputs, gates = map(int, header[1:6])
        if min(maximum, inputs, latches, outputs, gates) < 0 or latches or any(map(int, header[6:])):
            raise ValueError('unsupported AIGER model')
    except (ValueError, UnicodeError) as exc:
        raise ToolError(f'Invalid combinational AIGER: {exc}') from exc
    lines = outputs if header[0] == 'aig' else inputs + outputs + gates
    for _ in range(lines):
        next_end = data.find(b'\n', end)
        if next_end < 0:
            raise ToolError('Truncated AIGER numeric section')
        end = next_end + 1
    if header[0] == 'aig':
        for _ in range(2 * gates):
            while True:
                if end >= len(data):
                    raise ToolError('Truncated AIGER gate encoding')
                byte = data[end]
                end += 1
                if byte < 128:
                    break
    return hashlib.sha256(data[:end]).hexdigest()

def default_abc() -> Path:
    return Path(os.environ.get('ABC_BIN', str(ROOT / '.tools/usr/bin/yosys-abc'))).resolve()

@dataclass(frozen=True)
class Metrics:
    inputs: int
    outputs: int
    and_nodes: int
    aig_levels: int
    luts: int | None = None
    lut_levels: int | None = None

def parse_stats(output: str, mapped: bool = True) -> Metrics:
    text = ANSI.sub('', output)
    aig = None
    lut = None
    for line in text.splitlines():
        if line.startswith('ABC command line:'):
            continue
        io = re.search(r'i/o\s*=\s*(\d+)\s*/\s*(\d+).*?lat\s*=\s*(\d+)', line)
        level = re.search(r'\blev\s*=\s*(\d+)', line)
        if not io or not level:
            continue
        if int(io[3]) != 0:
            raise ToolError('Sequential circuit rejected: nonzero latch count')
        nodes = re.search(r'\band\s*=\s*(\d+)', line)
        nd = re.search(r'\bnd\s*=\s*(\d+)', line)
        if nodes:
            aig = (int(io[1]), int(io[2]), int(nodes[1]), int(level[1]))
        if nd:
            lut = (int(nd[1]), int(level[1]))
    if aig is None or (mapped and lut is None):
        raise ToolError('Missing ABC statistics; raw log retained')
    return Metrics(*aig, *(lut if mapped else (None, None)))

def cec_status(output: str, timed_out=False, returncode=0) -> str:
    if timed_out:
        return 'timeout'
    if returncode != 0:
        return 'error'
    clean = ANSI.sub('', output)
    if re.search(r'^Networks are NOT EQUIVALENT', clean, re.M | re.I):
        return 'not_equivalent'
    if re.search(r'^Networks are equivalent', clean, re.M):
        return 'equivalent'
    if re.search(r'(undecided|timeout|time limit|conflict limit)', clean, re.I):
        return 'unknown'
    return 'error'

def validate_aiger(p: Path):
    with p.open('rb') as f:
        header = f.readline().decode('ascii', errors='strict').split()
    if len(header) < 6 or header[0] not in ('aig', 'aag'):
        raise ValueError('Input must be an AIGER file')
    if int(header[3]) != 0:
        raise ValueError('Only combinational circuits are supported')
    # AIGER 1.9 safety/liveness properties are outside this course model.
    if any(int(v) for v in header[6:]):
        raise ValueError('AIGER property extensions are unsupported')

@dataclass
class Candidate:
    sequence: tuple[str, ...]
    metrics: Metrics
    aig: Path
    blif: Path | None
    fingerprint: str
    seconds: float
    log: str
    ident: int = -1

class ABC:
    def __init__(self, binary: Path | None = None, lut_size=6, timeout=30.0, memory_limit_bytes=None):
        self.binary = (binary or default_abc()).resolve()
        self.lut_size = int(lut_size)
        self.timeout = float(timeout)
        self.memory_limit_bytes = memory_limit_bytes
        self.deadline = None
        if memory_limit_bytes is not None and not shutil.which('prlimit'):
            raise ToolError('Memory-limited runs require Linux prlimit')
        if not self.binary.is_file():
            raise ToolError(f'ABC not found: {self.binary}. Run scripts/bootstrap.py first.')
        if not 2 <= self.lut_size <= 16:
            raise ValueError('LUT size must be between 2 and 16')
        # WSL's mounted Windows filesystem adds substantial executable paging cost.
        # Stage the exact same binary on Linux tmpfs/ext4 once per process.
        self._stage = tempfile.TemporaryDirectory(prefix='lut-abc-bin-')
        atexit.register(self._stage.cleanup)
        self.runtime_binary = Path(self._stage.name) / self.binary.name
        shutil.copyfile(self.binary, self.runtime_binary)
        self.runtime_binary.chmod(0o755)
        if sha256(self.runtime_binary) != sha256(self.binary):
            raise ToolError('Runtime binary staging checksum mismatch')

    def invoke(self, script: str, cwd: Path, timeout=None):
        # -s ignores user abc.rc so experiment behavior is independent of the machine.
        start = time.perf_counter()
        limit = self.timeout if timeout is None else timeout
        if self.deadline is not None:
            limit = min(limit, max(0.001, self.deadline-start))
        command = [str(self.runtime_binary), '-s', '-c', script]
        if self.memory_limit_bytes is not None:
            command = ['prlimit', f'--as={self.memory_limit_bytes}', '--', *command]
        cp = subprocess.run(command, cwd=cwd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=limit)
        elapsed = time.perf_counter() - start
        output = cp.stdout.decode('utf-8', errors='replace')
        return cp.returncode, output, elapsed

    def version(self):
        rc, log, _ = self.invoke('version', ROOT)
        if rc:
            raise ToolError(log)
        return ANSI.sub('', log).strip()

    def evaluate(self, source: Path, sequence, directory: Path, mapped=True, timeout=None) -> Candidate:
        sequence = tuple(sequence)
        if any(op not in OPS for op in sequence):
            raise ValueError('Sequence contains an operation outside the fixed search space')
        directory.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(source, directory / 'input.aig')
        # Save the AIG BEFORE mapping. Search must never optimize a mapped/round-tripped network.
        commands = ['read_aiger input.aig', 'strash', *sequence,
                    'print_stats', 'write_aiger -s candidate.aig']
        if mapped:
            commands += [f'if -K {self.lut_size}', 'print_stats', 'write_blif candidate.blif']
        script = '; '.join(commands)
        try:
            rc, log, seconds = self.invoke(script, directory, timeout)
        except subprocess.TimeoutExpired as exc:
            log = (exc.stdout or b'').decode('utf-8', errors='replace') + '\nPROCESS TIMEOUT\n'
            (directory / 'abc.log').write_text(log, encoding='utf-8')
            raise
        (directory / 'abc.log').write_text(log, encoding='utf-8')
        (directory / 'commands.abc').write_text(script + '\n', encoding='utf-8')
        if rc or not (directory / 'candidate.aig').is_file() or (mapped and not (directory / 'candidate.blif').is_file()):
            raise ToolError(log)
        stats = parse_stats(log, mapped)
        return Candidate(sequence, stats, directory / 'candidate.aig',
                         directory / 'candidate.blif' if mapped else None,
                         structure_sha256(directory / 'candidate.aig'), seconds, log)

    def map_candidate(self, candidate: Candidate, directory: Path, timeout=None) -> Candidate:
        result = self.evaluate(candidate.aig, (), directory, mapped=True, timeout=timeout)
        result.sequence = candidate.sequence
        return result

    def verify(self, reference: Path, result: Path, directory: Path, timeout=30.0):
        if result.suffix not in ('.aig', '.aag', '.blif'):
            raise ValueError('Verification accepts .aig, .aag or .blif files')
        directory.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(reference, directory / 'reference.aig')
        target = 'result' + result.suffix
        shutil.copyfile(result, directory / target)
        script = f'cec reference.aig {target}'
        start = time.perf_counter()
        try:
            rc, log, elapsed = self.invoke(script, directory, timeout)
            status = cec_status(log, returncode=rc)
        except subprocess.TimeoutExpired as exc:
            elapsed = time.perf_counter() - start
            log = (exc.stdout or b'').decode('utf-8', errors='replace')
            status = 'timeout'
        (directory / 'cec.log').write_text(log, encoding='utf-8')
        return {'status': status, 'seconds': elapsed, 'log': log}
