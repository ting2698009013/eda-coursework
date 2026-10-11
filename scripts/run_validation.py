"""Run the real test suite once and preserve its complete transcript and source identity."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lutsearch.abc import ROOT,default_abc,sha256
from lutsearch.campaign import utc
from lutsearch.experiment import source_hash
from lutsearch.search import write_json

p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args()
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
before=source_hash();started=utc();command=[sys.executable,'-m','unittest','discover','-s','tests','-v']
cp=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
(out/'tests.log').write_text(cp.stdout,encoding='utf-8')
match=re.search(r'Ran (\d+) tests in ([\d.]+)s',cp.stdout)
result=dict(command=command,started_utc=started,completed_utc=utc(),returncode=cp.returncode,
    passed=cp.returncode==0 and before==source_hash(),source_sha256=before,abc_sha256=sha256(default_abc()),
    tests=int(match[1]) if match else None,test_seconds=float(match[2]) if match else None)
write_json(out/'validation.json',result)
print(cp.stdout);print(json.dumps(result,indent=2))
raise SystemExit(0 if result['passed'] else 1)
