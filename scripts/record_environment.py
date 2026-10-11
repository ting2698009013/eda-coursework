"""Record the machine used for timing experiments (run inside the same WSL)."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform

parser = argparse.ArgumentParser()
parser.add_argument('run', type=Path)
args = parser.parse_args()
cpu_lines = Path('/proc/cpuinfo').read_text().splitlines()
cpu_model = next((line.split(':', 1)[1].strip() for line in cpu_lines if line.startswith('model name')), 'unavailable')
memory_lines = Path('/proc/meminfo').read_text().splitlines()
memory_kib = int(next(line.split()[1] for line in memory_lines if line.startswith('MemTotal:')))
record = {'recorded_utc': datetime.now(timezone.utc).isoformat(), 'cpu_model': cpu_model,
          'logical_cpus': os.cpu_count(), 'affinity_cpus': len(os.sched_getaffinity(0)),
          'wsl_memory_gib': round(memory_kib/1024**2, 2), 'platform': platform.platform(),
          'python_version': platform.python_version(),
          'note': 'Snapshot of the same WSL at recorded_utc; host background load is not controlled.'}
output = args.run.resolve()/'hardware.json'
output.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(record, indent=2, ensure_ascii=False))
