from __future__ import annotations
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from .abc import ABC, ROOT, ToolError, sha256, validate_aiger
from .search import METHODS, SearchConfig, build_reference, run_search

def prepare_verilog(source: Path, top: str, output: Path):
    if output.exists():
        raise FileExistsError(output)
    if not top.replace('_','').isalnum() or not top or top[0].isdigit():
        raise ValueError('Top module must be a simple Verilog identifier')
    yosys=Path(os.environ.get('YOSYS_BIN',str(ROOT/'.tools/usr/bin/yosys')))
    env=os.environ.copy()
    env['LD_LIBRARY_PATH']=str(ROOT/'.tools/usr/lib/x86_64-linux-gnu')+(':'+env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    with tempfile.TemporaryDirectory(prefix='lutrtl-') as td:
        td=Path(td);shutil.copyfile(source,td/'input.v')
        script=f'read_verilog input.v; hierarchy -check -top {top}; synth -top {top} -noabc; aigmap; write_aiger result.aig'
        cp=subprocess.run([str(yosys),'-p',script],cwd=td,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        log=cp.stdout.decode(errors='replace')
        output.parent.mkdir(parents=True,exist_ok=True)
        output.with_suffix('.yosys.log').write_text(log,encoding='utf-8')
        if cp.returncode or not (td/'result.aig').exists():
            raise ToolError(log)
        validate_aiger(td/'result.aig')
        shutil.copyfile(td/'result.aig',output)
    return {'output':str(output),'input_sha256':sha256(source),'aig_sha256':sha256(output)}

def main():
    parser=argparse.ArgumentParser(description='ABC 逻辑综合序列搜索与验证')
    parser.add_argument('--abc',type=Path,help='ABC executable (or ABC_BIN environment variable)')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('doctor',help='check tool and benchmark provenance')
    p=sub.add_parser('run',help='run a complete benchmark experiment')
    p.add_argument('--config',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--resume',action='store_true')
    p=sub.add_parser('search',help='optimize one combinational AIGER circuit')
    p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--method',choices=METHODS,default='lut_beam_diverse');p.add_argument('--calls',type=int,default=120)
    p.add_argument('--seconds',type=float);p.add_argument('--seed',type=int,default=0);p.add_argument('--depth',type=int,default=10)
    p.add_argument('--width',type=int,default=4)
    p.add_argument('--random-length',type=int)
    p.add_argument('--restart-after',type=int,default=10)
    p=sub.add_parser('prepare',help='convert combinational Verilog with local Yosys')
    p.add_argument('--verilog',type=Path,required=True);p.add_argument('--top',required=True);p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('verify',help='check AIG or BLIF against the original AIG')
    p.add_argument('--reference',type=Path,required=True);p.add_argument('--result',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('report',help='build figures and an offline HTML/Markdown report')
    p.add_argument('--run',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=='report':
            from .report import make_report
            print(make_report(args.run.resolve()));return
        if args.command=='prepare':
            print(json.dumps(prepare_verilog(args.verilog.resolve(),args.top,args.out.resolve()),ensure_ascii=False,indent=2));return
        abc=ABC(args.abc)
        if args.command=='doctor':
            manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
            for c in manifest['circuits']:
                assert sha256(ROOT/c['file'])==c['sha256'],c['name']
            print(json.dumps({'abc':str(abc.binary),'version':abc.version(),'abc_sha256':sha256(abc.binary),
                'benchmark_commit':manifest['benchmark_commit'],'checked_circuits':len(manifest['circuits'])},ensure_ascii=False,indent=2))
        elif args.command=='run':
            from .campaign import run_experiment
            result=run_experiment(args.config.resolve(),args.out.resolve(),abc,args.resume)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            if not result['all_valid']:sys.exit(2)
        elif args.command=='search':
            output=args.out.resolve()
            if output.exists():raise FileExistsError(output)
            output.mkdir(parents=True)
            build_reference(abc,args.input.resolve(),output/'reference')
            d=run_search(abc,args.input.resolve(),output/'reference',output/'search',
                SearchConfig(method=args.method,seed=args.seed,max_calls=args.calls,seconds=args.seconds,max_depth=args.depth,beam_width=args.width,
                             random_length=args.random_length,restart_after=args.restart_after))
            print(json.dumps(d,ensure_ascii=False,indent=2))
            if not d['valid_result']:sys.exit(2)
        elif args.command=='verify':
            result=abc.verify(args.reference.resolve(),args.result.resolve(),args.out.resolve())
            print(json.dumps(result,ensure_ascii=False,indent=2))
            if result['status']!='equivalent':sys.exit(2)
    except (ValueError,FileExistsError,FileNotFoundError,ToolError) as exc:
        parser.exit(2,str(exc)+'\n')

if __name__=='__main__':main()
