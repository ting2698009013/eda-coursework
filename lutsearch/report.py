"""Recompute every table and scientific figure from saved trial summaries and traces."""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import base64
import html
import json
import math
import statistics

LABELS={'random':'随机搜索','greedy':'贪心搜索','aig_beam':'AIG 指标束搜索',
        'lut_beam':'LUT 反馈束搜索','lut_beam_diverse':'LUT 反馈束搜索＋探索分支'}
SHORT={'random':'Random','greedy':'Greedy','aig_beam':'AIG beam','lut_beam':'LUT beam','lut_beam_diverse':'LUT beam + explore'}
COLORS={'random':'#89939d','greedy':'#bc923e','aig_beam':'#4274a8','lut_beam':'#008c90','lut_beam_diverse':'#8567b4'}

def table_html(headers, rows):
    return '<table><thead><tr>'+''.join('<th>'+html.escape(str(v))+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'

def table_md(headers,rows):
    return '\n'.join(['| '+' | '.join(map(str,headers))+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])

def aggregate(trials, circuits, methods, protocol):
    groups=defaultdict(list)
    for t in trials:
        if t['protocol']==protocol and t['valid_result']:
            groups[t['method'],t['circuit']].append(t)
    result=[]
    for method in methods:
        ratios=[];wins=0;beatrandom=0;medians={};times=[];calls=[]
        for circuit in circuits:
            batch=groups[method,circuit]
            if not batch:continue
            lut=statistics.median(t['winner']['metrics']['luts'] for t in batch)
            baseline=batch[0]['baseline']['metrics']['luts']
            ratio=lut/baseline if baseline else 1.0
            ratios.append(ratio);wins+=lut<baseline;medians[circuit]=lut
            times.extend(t['search_seconds'] for t in batch);calls.extend(t['search_calls'] for t in batch)
            randoms=groups['random',circuit]
            if randoms:
                beat_random=statistics.median(t['winner']['metrics']['luts'] for t in randoms)
                beatrandom+=lut<beat_random
        geom=math.exp(statistics.mean(math.log(x) for x in ratios)) if ratios else float('nan')
        result.append({'method':method,'protocol':protocol,'geomean_gain':100*(1-geom),
            'improved_circuits':wins,'better_than_random':beatrandom,'circuits':len(ratios),
            'mean_seconds':statistics.mean(times) if times else None,
            'mean_calls':statistics.mean(calls) if calls else None,'median_luts':medians})
    return result

def make_report(run: Path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    metadata=json.loads((run/'experiment.json').read_text(encoding='utf-8'))
    completion=json.loads((run/'completion.json').read_text(encoding='utf-8'))
    spec=metadata['identity']['spec']
    circuits=spec['circuits'];methods=spec['methods'];protocols=[p['name'] for p in spec['protocols']]
    trials=[]
    for p in sorted((run/'trials').glob('*/summary.json')):
        t=json.loads(p.read_text(encoding='utf-8'));t['_path']=p.parent;trials.append(t)
    out=run/'report';out.mkdir(exist_ok=True)
    figures=out/'figures';figures.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
                         'axes.spines.right':False,'figure.dpi':140,'savefig.dpi':180})
    aggregates={p:aggregate(trials,circuits,methods,p) for p in protocols}
    (out/'aggregates.json').write_text(json.dumps(aggregates,ensure_ascii=False,indent=2),encoding='utf-8')
    valid=sum(bool(t['valid_result']) for t in trials)
    all_valid=completion['all_valid'] and valid==len(trials)
    title='逻辑综合序列搜索实验报告'
    intro=f'本次实验包含 {len(circuits)} 个电路、{len(methods)} 种方法和 {len(spec["seeds"])} 个种子，共计划 {completion["planned_trials"]} 次运行，完成 {len(trials)} 次。通过 AIG 与 LUT 网表两次等价检查并满足深度约束的结果为 {valid} 次。'
    sections=[f'<h1>{title}</h1><p class="lead">{intro}</p>']
    md=[f'# {title}',intro]
    if not all_valid:
        warning='本次实验存在未完成或无效结果，不能直接将下列表格视为完整实验结论。'
        sections.append('<p class="warning">'+warning+'</p>');md.append(warning)
    time_protocol=next((p['name'] for p in spec['protocols'] if p.get('seconds') is not None),protocols[0])
    best=min(aggregates[time_protocol],key=lambda d:-d['geomean_gain'])
    finding=f'在 {time_protocol} 协议下，按各电路跨种子中位数汇总，{LABELS[best["method"]]}取得最高的几何平均 LUT 降幅，为 {best["geomean_gain"]:.2f}%。这是当前测试集和预算下的观察，不代表对其他电路或更长搜索时间的保证。'
    sections.append('<h2>主要观察</h2><p>'+finding+'</p>');md+=['## 主要观察',finding]
    definitions='主目标是在 LUT 层数不超过 resyn2 结果时减少 LUT 数。所有搜索都保留 resyn2 作为可回退输出，因此零提升表示搜索未找到更好的可行方案。汇总先对每个电路的不同种子取 LUT 数中位数，再对“搜索结果 / resyn2”取几何平均，避免大电路支配结论。'
    sections.append('<p>'+definitions+'</p>');md.append(definitions)

    for protocol in protocols:
        ag=aggregates[protocol]
        rows=[[LABELS[d['method']],f'{d["geomean_gain"]:.2f}%',f'{d["improved_circuits"]}/{d["circuits"]}',
               f'{d["better_than_random"]}/{d["circuits"]}',f'{d["mean_seconds"]:.3f}',f'{d["mean_calls"]:.1f}'] for d in ag]
        headers=['方法','几何平均 LUT 降幅','优于 resyn2 的电路','优于随机搜索的电路','平均搜索秒数','平均 ABC 调用次数']
        sections.append(f'<h2>{html.escape(protocol)} 结果</h2>'+table_html(headers,rows))
        md += [f'## {protocol} 结果',table_md(headers,rows)]
        fig,ax=plt.subplots(figsize=(9.5,4.2),layout='constrained')
        ax.bar([SHORT[d['method']] for d in ag],[d['geomean_gain'] for d in ag],color=[COLORS[d['method']] for d in ag])
        ax.set_ylabel('Geometric-mean LUT reduction (%)');ax.set_title(protocol+' / per-circuit median over seeds');ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
        for i,d in enumerate(ag):ax.text(i,d['geomean_gain']+.1,f'{d["geomean_gain"]:.2f}%',ha='center',va='bottom',fontsize=9)
        ax.set_ylim(0,max(1,max(d['geomean_gain'] for d in ag))*1.2)
        file=figures/f'{protocol}_aggregate.png';fig.savefig(file);plt.close(fig)
        sections.append('<img src="figures/'+file.name+'" alt="各方法汇总对比">');md.append(f'![各方法汇总对比](figures/{file.name})')
        matrix=[];detail=[]
        for c in circuits:
            base=next(t['baseline']['metrics']['luts'] for t in trials if t['circuit']==c)
            lutrow=[d['median_luts'].get(c,float('nan')) for d in ag]
            gains=[100*(1-x/base) for x in lutrow]
            matrix.append(gains);detail.append([c,base]+[f'{x:g}' for x in lutrow])
        fig,ax=plt.subplots(figsize=(10,max(3,len(circuits)*.43+1)),layout='constrained')
        mat=np.array(matrix);im=ax.imshow(mat,cmap='YlGnBu',vmin=0,vmax=max(1,float(np.nanmax(mat))),aspect='auto')
        ax.set_xticks(range(len(methods)),[SHORT[m] for m in methods]);ax.set_yticks(range(len(circuits)),circuits)
        for r in range(len(circuits)):
            for c in range(len(methods)):
                ax.text(c,r,f'{mat[r,c]:.1f}%',ha='center',va='center',fontsize=9,color='white' if mat[r,c] > np.nanmax(mat)*.65 else '#132a39')
        fig.colorbar(im,ax=ax,label='LUT reduction (%)',shrink=.8);ax.set_title(protocol+' / all preselected circuits')
        file=figures/f'{protocol}_circuits.png';fig.savefig(file);plt.close(fig)
        sections.append('<img src="figures/'+file.name+'" alt="全部电路的结果">'+table_html(['电路','resyn2 LUT']+[SHORT[m] for m in methods],detail))
        md += [f'![全部电路的结果](figures/{file.name})',table_md(['电路','resyn2 LUT']+[SHORT[m] for m in methods],detail)]

    # Display ALL convergence traces at seed zero, not a selected best case.
    for protocol in protocols:
        ncols=2;nrows=math.ceil(len(circuits)/ncols)
        fig,axes=plt.subplots(nrows,ncols,figsize=(12,2.5*nrows),layout='constrained',squeeze=False)
        seconds=next(p.get('seconds') for p in spec['protocols'] if p['name']==protocol)
        for idx,circuit in enumerate(circuits):
            ax=axes.flat[idx]
            for method in methods:
                match=[t for t in trials if t['circuit']==circuit and t['method']==method and t['protocol']==protocol and t['seed']==spec['seeds'][0]]
                if not match:continue
                t=match[0];trace=[json.loads(x) for x in (t['_path']/'trace.jsonl').read_text(encoding='utf-8').splitlines()]
                x=[0]+[r['elapsed_seconds'] if seconds is not None else r['call'] for r in trace]
                b=t['baseline']['metrics']['luts'];y=[0]+[100*(b-r['incumbent_luts'])/b for r in trace]
                ax.step(x,y,where='post',color=COLORS[method],label=SHORT[method],linewidth=1.3)
            ax.set_title(circuit);ax.set_xlabel('Search time (s)' if seconds is not None else 'ABC invocations');ax.set_ylabel('LUT reduction (%)');ax.grid(alpha=.2)
        for idx in range(len(circuits),ncols*nrows):axes.flat[idx].set_visible(False)
        handles,labels=axes.flat[0].get_legend_handles_labels();fig.legend(handles,labels,loc='outside lower center',ncol=3,frameon=False)
        file=figures/f'{protocol}_convergence.png';fig.savefig(file);plt.close(fig)
        sections.append(f'<h2>{protocol} 搜索过程</h2><p>展示全部电路、种子 {spec["seeds"][0]} 的轨迹。其余种子的原始记录保存在各 trial 目录。</p><img src="figures/{file.name}" alt="全部电路收敛曲线">')
        md += [f'## {protocol} 搜索过程',f'![全部电路收敛曲线](figures/{file.name})']

    stronger=[]
    for circuit in circuits:
        r=json.loads((run/'references'/circuit/'reference.json').read_text(encoding='utf-8'))['records']
        b=r['resyn2']['metrics'];twice=r['resyn2x2']['metrics']
        stronger.append([circuit,b['luts'],b['lut_levels'],twice['luts'],twice['lut_levels'],
                         '是' if twice['lut_levels']<=b['lut_levels'] else '否'])
    h=['电路','resyn2 LUT','resyn2 层数','resyn2×2 LUT','resyn2×2 层数','满足主深度约束']
    sections.append('<h2>更长固定脚本参考</h2><p>另外运行两遍 resyn2，防止只与单一短脚本比较。该脚本长 20 步，超出搜索序列最长 10 步的范围，仅作为额外参考。</p>'+table_html(h,stronger));md+=['## 更长固定脚本参考',table_md(h,stronger)]

    method_notes=[
      '输入在实验前固定。开发集 ctrl、int2float 与正式测试集分开，正式测试保留所有预选电路和全部结果。',
      '随机搜索采样长度为 10 的序列。贪心搜索每轮保留一个候选。束搜索默认宽度为 4，最大深度为 10。',
      '贪心、AIG 束搜索和普通 LUT 束搜索在调用次数协议下是确定性的，三个种子用于重复性检查；时间协议的差异还会受到进程开销和系统负载影响。随机搜索与探索分支方法使用种子控制随机选择。',
      'AIG 方法先只计算 AIG 指标，再对保留的候选做 LUT 映射。映射调用同样计入预算。LUT 方法对每个候选执行映射后再选择。',
      '探索分支方法保留 3 个排名靠前候选，另从剩余不同结构候选中随机保留 1 个。相同功能或相同指标不视为同一结构。',
      '调用次数协议限制 ABC 进程调用总数，并非等量 CPU 工作或等量完整映射。等时间协议更适合评价实际效率。',
      '搜索时间包含进程启动、临时文件和候选选择。共同基准准备、最终网表导出和等价检查另行记录。到达截止时间会终止当前调用，进程回收可能造成毫秒级超出。',
      'AIG 在映射前保存，并保留端口名称。最终同时检查优化 AIG 与映射 BLIF 是否和原输入等价。超时、未知和工具错误都不计为通过。',
      '候选按 AIGER 电路数据部分的 SHA-256 去重，排除端口名称和时间注释；另存完整文件的校验和用于检查文件完整性。',
      '结果只适用于所列电路、工具版本和预算。LUT 数与层数不能替代具体 FPGA 布局布线后的资源、频率或功耗。',
      '初次开发试运行曾因 AIG 导出丢失端口名而验证失败，修复为 write_aiger -s 后重新运行。旧结果位于 results/smoke，仅用于调试，不纳入本报告。'
      ,'另一轮试运行发现整文件去重受 AIG 时间注释影响，修复为电路数据去重后重新运行。旧目录 results/study_20261009 已作废，不纳入本报告。'
    ]
    sections.append('<h2>方法与边界</h2><ul>'+''.join('<li>'+html.escape(x)+'</li>' for x in method_notes)+'</ul>');md+=['## 方法与边界']+['- '+x for x in method_notes]
    reproducibility=f'ABC SHA-256: {metadata["identity"]["abc_sha256"]}\nBenchmark commit: {metadata["benchmark_commit"]}\nCode SHA-256: {metadata["identity"]["source_sha256"]}\nStarted UTC: {metadata["started_utc"]}\n{metadata["abc_version"]}'
    sections.append('<h2>版本与复现</h2><pre>'+html.escape(reproducibility)+'</pre><p><a href="../summary.csv">全部运行数据 CSV</a> · <a href="../experiment.json">实验配置和版本</a> · <a href="../completion.json">完成状态</a></p>');md+=['## 版本与复现','```text\n'+reproducibility+'\n```','[全部运行数据](../summary.csv)']
    references=[('ABC 官方项目','https://github.com/berkeley-abc/abc'),('EPFL 基准','https://github.com/lsils/benchmarks'),('FlowTune 论文','https://arxiv.org/abs/2202.07721')]
    sections.append('<h2>参考资料</h2><ul>'+''.join(f'<li><a href="{u}">{t}</a></li>' for t,u in references)+'</ul>');md+=['## 参考资料']+[f'- [{t}]({u})' for t,u in references]
    css='body{font-family:"Microsoft YaHei",system-ui,sans-serif;color:#19334b;background:#f8fafc;margin:0}main{max-width:1120px;margin:0 auto;padding:45px 38px;background:white}h1{font-size:32px}h2{margin-top:42px;font-size:24px;color:#007d87}.lead{font-size:18px}p,li{line-height:1.8}table{width:100%;border-collapse:collapse;font-size:14px;margin:22px 0}th,td{border-bottom:1px solid #dce5eb;padding:11px;text-align:left}th{background:#edf5f6}img{max-width:100%;margin:18px 0}pre{white-space:pre-wrap;word-break:break-all;background:#f3f6f9;padding:20px;font-size:12px}a{color:#007d87}.warning{color:#a12929;font-weight:bold}li{margin-bottom:9px}@media print{main{padding:0}h2{break-after:avoid}img,table{break-inside:avoid}}'
    document='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>'+css+'</style><main>'+''.join(sections)+'</main></html>'
    (out/'report.html').write_text(document,encoding='utf-8')
    (out/'REPORT.md').write_text('\n\n'.join(md)+'\n',encoding='utf-8')
    return out/'report.html'
