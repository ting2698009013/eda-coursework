"""Build a Chinese report and static scientific charts from completed audited stages."""
import argparse
import collections
from datetime import datetime
import html
import json
import math
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lutsearch.statistics import paired_comparison
from lutsearch.search import write_json

NAMES={'random':'随机搜索','lut_beam':'LUT 束搜索','aig_beam':'AIG 束搜索',
       'greedy':'贪心搜索','lut_beam_diverse':'探索分支束搜索','sequence_local':'整序列局部搜索',
       'aig_beam_mapped':'AIG 评分（全部映射）',
       **{f'random_len{n}':f'随机搜索，长度 {n}' for n in (3,5,10)},
       **{f'local_restart{n}':f'局部搜索，停滞 {n} 次重启' for n in (5,10,20)}}
STAGES=['e1_stability','e2_budget','e3_length','e3_score','e4_development','e5_holdout']
STAGE_NAMES={'e3_length':'E3：序列长度对照','e3_score':'E3：评分对照','e4_development':'E4：开发结果'}
STOPS={'completed_tree':'完成规定搜索流程','time_budget':'时间用尽','call_budget':'调用数用尽',
       'completed_space':'已尝试全部序列','no_expandable_candidates':'无可扩展候选','attempt_limit':'试探次数保护上限'}

def load(path):return json.loads(path.read_text(encoding='utf-8'))
def preflight_reason(record):
    reason=record.get('reason','通过')
    if reason=="ToolError('Reference unoptimized did not pass both CEC checks: timeout, equivalent')":
        return '直接映射 BLIF 的 CEC 超过 120 秒；AIG 等价检查通过'
    return reason
def records(path):return [load(p) for p in sorted(path.glob('trials/*/summary.json'))]
def table(headers,rows):
    return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+''.join('| '+' | '.join(map(str,r))+' |\n' for r in rows)
def interval(d):
    if d.get('status')!='ok':return '未生成（缺失/无效结果或未设主比较）'
    return f'{d["delta_percent"]:+.2f}% [{d["ci95_percent"][0]:+.2f}%, {d["ci95_percent"][1]:+.2f}%]'

def sensitivity(d):
    v=d.get('circuit_composition_sensitivity95')
    return (f'对电路组成重新抽样的敏感性范围为 [{v[0]:+.2f}%, {v[1]:+.2f}%]。'
            '它用于观察结论是否依赖电路构成；这些电路并非所有 FPGA 电路的随机样本，不能把该范围当作普遍推广保证。\n') if v else ''

def relative_point(ds,candidate,reference):
    cells=collections.defaultdict(list)
    for d in ds:
        if d['method'] in (candidate,reference):
            cells[(d['circuit'],d['method'])].append(math.log(d['winner']['metrics']['luts']))
    circuits=sorted({c for c,m in cells})
    return 100*(1-math.exp(statistics.mean(statistics.mean(cells[(c,candidate)])-statistics.mean(cells[(c,reference)]) for c in circuits)))

def build(base):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],'axes.unicode_minus':False,
                         'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.dpi':140})
    report=base/'report';figures=report/'figures';figures.mkdir(parents=True,exist_ok=True)
    completed={}
    for name in STAGES:
        p=base/name
        if (p/'phase.json').exists() and load(p/'phase.json')['phase'] in ('complete','complete_with_issues'):
            completed[name]=dict(path=p,analysis=load(p/'analysis.json'),records=records(p))
    text=['# 扩展实验报告\n','本报告汇总已完成处理的阶段，验证、审计或重放未通过时明确标出并暂停主比较。各表注明比较对象；LUT 降幅为正表示使用更少的 LUT，为负表示更多。\n']
    if (base/'decisions.json').exists():
        text+=['## 主要发现\n']
        for key,value in load(base/'decisions.json').items():text.append(f'### {key}\n\n{value}\n')
    inventory=[]
    for name,data in completed.items():
        c=data['analysis']['completion'];a=load(data['path']/'audit.json');r=load(data['path']/'replay_audit.json')
        phase=load(data['path']/'phase.json')
        full_minutes=(datetime.fromisoformat(phase['completed_utc'])-datetime.fromisoformat(phase['started_utc'])).total_seconds()/60
        inventory.append([name,c['completed_trials'],c['planned_trials'],str(c['all_valid']),str(a['passed']),str(r['passed']),f'{c["wall_seconds"]/60:.1f}',f'{full_minutes:.1f}'])
    text+=['## 完成情况\n',table(['阶段','完成','计划','结果有效','审计','重放','运行分钟','含审计重放分钟'],inventory)]
    findings={}
    if 'e1_stability' in completed:
        e1=completed['e1_stability'];d=e1['analysis']['primary']['time2s'];findings['E1']=d
        text+=['## E1：2 秒下的稳定性\n',f'普通 LUT 束搜索相对随机搜索：**{interval(d)}**。区间固定当前 10 个诊断电路，只反映种子与计时重复波动。\n']
        text.append(sensitivity(d))
        ds=e1['records'];circuits=sorted({r['circuit'] for r in ds});points=[];rows=[]
        for c in circuits:
            pairs=collections.defaultdict(dict)
            for r in ds:
                if r['circuit']==c and r['method'] in ('random','lut_beam'):
                    pairs[r['block_id']][r['method']]=r['winner']['metrics']['luts']
            vals=[100*(1-p['lut_beam']/p['random']) for p in pairs.values() if len(p)==2];points.append(vals)
            pc=d.get('per_circuit',{}).get(c,{})
            rows.append([c,f'{pc.get("delta_percent",float("nan")):+.2f}%',pc.get('wins'),pc.get('ties'),pc.get('losses')])
        text.append(table(['电路','相对随机搜索','胜','平','负'],rows))
        secondary=[]
        for m in sorted({r['method'] for r in ds}):
            per_case=[];sizes=[]
            for c in circuits:
                subset=[r for r in ds if r['circuit']==c and r['method']==m]
                sizes.append(len(subset));per_case.append(statistics.mean(math.log(r['winner']['metrics']['luts']/r['baseline']['metrics']['luts']) for r in subset))
            secondary.append([NAMES.get(m,m),min(sizes),f'{100*(1-math.exp(statistics.mean(per_case))):.2f}%'])
        text+=['### 其他方法的描述性结果\n','下表相对 resyn2 汇总，各电路等权。重复数不同的方法只作描述性对照，不从中重新挑选主比较。\n',
               table(['方法','每电路重复次数','相对 resyn2 的 LUT 降幅'],secondary)]
        fig,ax=plt.subplots(figsize=(9,5.8));ax.boxplot(points,vert=False,showmeans=True);ax.set_yticks(range(1,len(circuits)+1),circuits)
        ax.axvline(0,color='#546678',lw=1);ax.set_xlabel('LUT 束搜索相对随机搜索节省的 LUT（%）')
        ax.set_title('30 个时段块：逐电路结果分布（正数更好）');ax.grid(axis='x',alpha=.2)
        fig.tight_layout();fig.savefig(figures/'e1_distribution.png');plt.close(fig)
        text.append('![E1 分布](figures/e1_distribution.png)\n')
        text.append('图中箱体覆盖中间一半结果，橙线为中位数，绿色三角为平均值；圆点为箱线图规则标出的离群点，仍计入统计。\n')
    if 'e2_budget' in completed and 'e1_stability' in completed:
        e2=completed['e2_budget'];ds2=e2['records'];ds1=completed['e1_stability']['records']
        comparisons={2:paired_comparison([d for d in ds1 if d['method'] in ('random','lut_beam') and d['replicate_id']<10])}
        comparisons.update({s:e2['analysis']['primary'][f'time{s}s'] for s in (10,30)});findings['E2']=comparisons
        text+=['## E2：计算预算\n','每个时间点主方法各 10 次；2 秒点取 E1 预定前 10 个块。不同预算分别运行，属于探索性比较。\n',
               table(['预算','LUT 束搜索相对随机搜索及 95% 区间'],[[f'{s} 秒',interval(d)] for s,d in comparisons.items()])]
        fig,axes=plt.subplots(1,2,figsize=(11,4.5));x=list(comparisons);y=[comparisons[s]['delta_percent'] for s in x]
        axes[0].errorbar(x,y,yerr=[[y[i]-comparisons[s]['ci95_percent'][0] for i,s in enumerate(x)],
            [comparisons[s]['ci95_percent'][1]-y[i] for i,s in enumerate(x)]],fmt='o-',capsize=4,color='#176d91')
        axes[0].axhline(0,color='#777',lw=1);axes[0].set_ylabel('相对随机搜索节省 LUT（%）');axes[0].set_title('预算增加后，差距如何变化')
        depthrows=[]
        for m in ('random','lut_beam','aig_beam','greedy'):
            values=[]
            for s in x:
                source=ds1 if s==2 else ds2
                subset=[d for d in source if d['method']==m and d['protocol']==f'time{s}s' and d['replicate_id']<(10 if m in ('random','lut_beam') else 5)]
                values.append(statistics.mean(d['max_evaluated_depth'] for d in subset))
                depthrows.append([s,NAMES[m],f'{values[-1]:.2f}',sum(d['stop_reason']=='completed_tree' for d in subset),len(subset)])
            axes[1].plot(x,values,'o-',label=NAMES[m])
        for ax in axes:ax.set_xticks(x);ax.set_xlabel('搜索预算（秒）');ax.grid(alpha=.2)
        axes[1].set_ylabel('平均最大成功评估序列长度');axes[1].set_ylim(0,10.5);axes[1].legend(fontsize=8);axes[1].set_title('是否来得及尝试较长序列')
        fig.tight_layout();fig.savefig(figures/'e2_budget_depth.png');plt.close(fig)
        text+=['![预算与深度](figures/e2_budget_depth.png)\n',table(['秒','方法','平均已评估序列长度','完成规定搜索流程数','运行数'],depthrows),
               '这里的长度是优化操作数，不是电路的 LUT 层数。AIG 方法的已评估长度包含只完成 AIG 评分的候选。完成搜索流程指完成当前束宽和长度配置，不能理解为穷尽所有可能序列。\n']
        efficiency=[]
        for s in x:
            source=ds1 if s==2 else ds2
            for m in ('random','lut_beam','aig_beam','greedy'):
                subset=[d for d in source if d['method']==m and d['protocol']==f'time{s}s' and d['replicate_id']<(10 if m in ('random','lut_beam') else 5)]
                efficiency.append([s,NAMES[m],f'{statistics.mean(d["search_seconds"] for d in subset):.2f}',
                    f'{statistics.mean(d["successful_evaluations"] for d in subset):.1f}',
                    f'{statistics.mean(d["mapping_evaluations"] for d in subset):.1f}',
                    f'{statistics.mean(d["successful_evaluations"]/max(d["search_seconds"],1e-9) for d in subset):.1f}'])
        text+=['### 实际计算开销\n',table(['预算秒','方法','平均搜索秒','平均成功调用','平均映射次数','平均成功调用/秒'],efficiency),
            '成功调用包含 AIG 评分或映射，不能把不同类型的调用当作相同工作量；比较最终质量时仍采用相同墙钟预算。固定参考、导出和最终等价检查在搜索预算之外，原始记录分别保存其耗时。\n']
    for name in ('e3_length','e3_score','e4_development'):
        if name in completed:
            data=completed[name];text+=[f'## {STAGE_NAMES[name]}（探索性）\n',
                table(['电路','方法','次数','LUT 中位数','范围','平均已评估序列长度'],[
                    [d['circuit'],NAMES.get(d['method'],d['method']),d['n'],d['median'],f'{d["min"]}–{d["max"]}',f'{d["mean_depth"]:.2f}']
                    for d in data['analysis']['descriptive']])]
            if name=='e4_development' and (base/'e4_selection.json').exists():
                choice=load(base/'e4_selection.json')
                text+=[table(['停滞阈值','相对随机搜索的 LUT 降幅'],[[r['config']['restart_after'],f'{r["delta_percent"]:+.2f}%'] for r in choice['scores']]),
                    f'按运行前规则选中停滞阈值 **{choice["selected"]["config"]["restart_after"]}**：距最高降幅不超过 0.10 个百分点时，选较大的阈值以减少重启。开发结果用于选择配置，最终性能证据来自 E5。\n',
                    '新方法先在预算内重新评估 resyn2，之后单次替换一个操作，接受目标不变差的修改，停滞后随机重启。所得效果包括初始化、反馈和重启共同作用。\n']
    if (base/'mechanism_analysis.json').exists():
        mechanism=load(base/'mechanism_analysis.json')
        text+=['## E3 的机制证据\n',
            table(['案例','长度 10 相对长度 3 的 LUT 降幅'],[[c,f'{v:+.2f}%'] for c,v in mechanism['length10_vs3_percent'].items()]),
            '这三个案例按既有现象挑选。长度变化同时影响单次开销、可达结构和有限序列空间大小，不把这项对照解释成唯一原因的证明。\n',
            table(['案例','随机序列长度','平均成功映射次数','映射候选可行比例','平均成功调用秒','穷尽该长度序列的运行数'],[
                [r['circuit'],r['method'].replace('random_len',''),f'{r["mean_mapped"]:.1f}',f'{r["mean_feasible_fraction"]:.1%}',
                 f'{r["mean_command_seconds"]:.3f}',f'{r["stops"].get("completed_space",0)}/{r["n"]}']
                for r in sorted(mechanism['length_aggregate'],key=lambda r:(r['circuit'],int(r['method'].replace('random_len',''))))]),
            '所有长度设置都保留同一个 resyn2 回退结果；resyn2 本身有 10 个操作。因此，尝试完全部三步序列，并不意味着最终输出一定来自三步序列，也不能把含回退的最终 LUT 数直接称为三步空间的最优值。\n',
            table(['案例','评分','最终 LUT','达到长度','停止原因','剪掉全部层内最优可行候选的层数'],[
                [r['circuit'],NAMES.get(r['method'],r['method']),r['luts'],r['max_depth'],STOPS.get(r['stop'],r['stop']),r['layers_pruning_all_best_feasible']] for r in mechanism['score_results']]),
            '两种评分对照都映射全部候选，并把已经发现的可行最优结果保留为全局冠军。剪枝影响后续搜索路径，不会丢掉已经发现的最优输出；因此不能仅由某层剪枝推断最终结果必然变差。\n']
    if (base/'holdout_preflight/summary.json').exists():
        pre=load(base/'holdout_preflight/summary.json')
        text+=['## 新电路可运行性\n','8 个候选电路全部披露。准入按输入支持、固定脚本验证和预定资源限制决定，不按优化收益筛选。内存限制为 8 GiB 虚拟地址空间。\n',
            table(['电路','可纳入','秒','子进程峰值 RSS / MiB','原因'],[[r['circuit'],r['eligible'],round(r.get('seconds',0),2),
                round(r['peak_child_rss_kib']/1024,1) if 'peak_child_rss_kib' in r else '未采集',preflight_reason(r)] for r in pre['records']])]
    if 'e5_holdout' in completed and completed['e5_holdout']['analysis']['primary']['time10s'].get('status')!='ok':
        data=completed['e5_holdout'];c=data['analysis']['completion']
        text+=['## E5：验证问题与统计限制\n',
            '已完成预定条目的处理和已生成输出的重放，但存在未满足原定验证、审计或重放要求的结果。保留全部失败记录，不删除失败样本后计算优势；本阶段不生成无条件的主比较区间。\n',
            f'计划 {c["planned_trials"]} 次，生成摘要 {c["completed_trials"]} 次；初次无效结果 {len(c.get("invalid_trials",[]))} 次，程序异常 {len(c.get("failures",[]))} 次。详见 e5_holdout/completion.json、audit.json 和 replay_audit.json。\n']
        findings['E5']=data['analysis']['primary']['time10s']
    if 'e5_holdout' in completed:
        costs=[];source=completed['e5_holdout']['records']
        for circuit in sorted({r['circuit'] for r in source}):
            rs=[r for r in source if r['circuit']==circuit]
            search=statistics.mean(r['search_seconds'] for r in rs)
            cec=statistics.mean(r['cec_seconds'] for r in rs)
            total=statistics.mean(r['wall_seconds'] for r in rs)
            costs.append(dict(circuit=circuit,n=len(rs),search=search,cec=cec,total=total,other=max(0,total-search-cec)))
        text+=['## E5：搜索以外的验证成本\n',
            f'下表汇总三种方法生成摘要的全部 {len(source)} 次运行，其中初次验证未通过 {sum(not r["valid_result"] for r in source)} 次。公共参考准备和后续独立重放不在单次运行耗时中；没有摘要的程序异常条目在完成记录中另列。\n',
            table(['电路','次数','平均搜索秒','平均最终 CEC 秒','平均单次总秒'],[[r['circuit'],r['n'],f'{r["search"]:.2f}',f'{r["cec"]:.2f}',f'{r["total"]:.2f}'] for r in costs])]
        fig,ax=plt.subplots(figsize=(10,4.5));labels=[r['circuit'] for r in costs]
        searches=[r['search'] for r in costs];proofs=[r['cec'] for r in costs]
        ax.bar(labels,searches,label='搜索',color='#247b9d')
        ax.bar(labels,proofs,bottom=searches,label='最终等价检查',color='#d9923b')
        ax.bar(labels,[r['other'] for r in costs],bottom=[a+b for a,b in zip(searches,proofs)],label='其他开销',color='#97a6b2')
        ax.set_ylabel('平均单次运行时间（秒）');ax.set_title('10 秒搜索预算不等于 10 秒端到端运行');ax.legend();ax.grid(axis='y',alpha=.2)
        fig.tight_layout();fig.savefig(figures/'e5_time_cost.png');plt.close(fig)
        text.append('![搜索与验证时间](figures/e5_time_cost.png)\n')
    if 'e5_holdout' in completed and completed['e5_holdout']['analysis']['primary']['time10s'].get('status')=='ok':
        e5=completed['e5_holdout'];d=e5['analysis']['primary']['time10s'];findings['E5']=d
        text+=['## E5：新电路最终评价\n',f'冻结方法“{NAMES.get(d.get("candidate"),d.get("candidate"))}”相对随机搜索：**{interval(d)}**。\n',
               '这是预定 10 秒预算下、通过资源预检查的新电路上的结果，不能扩展为所有 FPGA 电路或真实频率、功耗的结论。\n']
        text.append(sensitivity(d))
        text.append(table(['电路','新方法相对随机搜索的 LUT 降幅','胜','平','负'],[
            [c,f'{r["delta_percent"]:+.2f}%',r['wins'],r['ties'],r['losses']] for c,r in d['per_circuit'].items()]))
        if d.get('candidate')!='lut_beam':
            text.append(f'次要对照点估计：原 LUT 束搜索相对随机搜索节省 {relative_point(e5["records"],"lut_beam","random"):+.2f}%；新方法相对原 LUT 束搜索节省 {relative_point(e5["records"],d["candidate"],"lut_beam"):+.2f}%。这些对照用于解释新方法的增量价值，不改变预先指定的主比较。\n')
        text.append(table(['电路','方法','次数','LUT 中位数','范围','回退率'],[[r['circuit'],NAMES.get(r['method'],r['method']),r['n'],r['median'],
            f'{r["min"]}–{r["max"]}',f'{r["fallback_rate"]:.0%}'] for r in e5['analysis']['descriptive']]))
        fixed=[];best_fixed={}
        for reference in sorted((e5['path']/'references').glob('*/reference.json')):
            rr=load(reference)['records'];limit=rr['resyn2']['metrics']['lut_levels']
            feasible_refs=[(r['metrics']['luts'],r['metrics']['lut_levels'],name) for name,r in rr.items() if r['metrics']['lut_levels']<=limit]
            best_fixed[reference.parent.name]=min(feasible_refs)
            for name in ('unoptimized','resyn2','resyn2x2'):
                m=rr[name]['metrics'];fixed.append([reference.parent.name,name,m['luts'],m['lut_levels'],m['lut_levels']<=limit])
        text+=['### 固定脚本参考\n','resyn2×2 含 20 个操作，是搜索空间之外的强参考；表中同时标明它是否满足当前深度约束。相对随机搜索有收益，也不自动意味着超过这些固定脚本。\n',
               table(['电路','固定脚本','LUT','LUT 层数','满足深度约束'],fixed)]
        reference_rows=[];fixed_log_ratios=[]
        for c,(luts,levels,name) in sorted(best_fixed.items()):
            subset=[r for r in e5['records'] if r['circuit']==c and r['method']==d['candidate']]
            q=statistics.mean(math.log(r['winner']['metrics']['luts']/luts) for r in subset);fixed_log_ratios.append(q)
            reference_rows.append([c,name,luts,f'{100*(1-math.exp(q)):+.2f}%',
                sum(r['winner']['metrics']['luts']<luts for r in subset),len(subset)])
        text+=[f'逐电路取三个固定脚本中满足深度约束且 LUT 最少的一项，冻结方法相对这一参考的等权 LUT 降幅为 **{100*(1-math.exp(statistics.mean(fixed_log_ratios))):+.2f}%**（描述性点估计）。此比较揭示搜索是否超越简单固定脚本；固定参考的准备开销单独记录，没有纳入 10 秒搜索预算。\n',
            table(['电路','最佳可行固定脚本','参考 LUT','冻结方法相对降幅','严格胜过参考次数','运行数'],reference_rows)]
        fixed_method_rows=[]
        for method in ('random','lut_beam',d['candidate']):
            log_ratios=[statistics.mean(math.log(r['winner']['metrics']['luts']/ref[0]) for r in e5['records'] if r['circuit']==c and r['method']==method)
                        for c,ref in sorted(best_fixed.items())]
            fixed_method_rows.append([NAMES.get(method,method),f'{100*(1-math.exp(statistics.mean(log_ratios))):+.2f}%'])
        text+=[table(['方法','相对最佳可行固定脚本的 LUT 降幅'],fixed_method_rows),
            '上表均为同一组电路等权的描述性点估计。超过固定脚本说明尝试不同序列有收益；只有再超过同预算随机搜索，才能支持新策略带来了额外价值。\n']
        candidate=d.get('candidate');cases=sorted({x['circuit'] for x in e5['records']});points=[]
        for c in cases:
            pairs=collections.defaultdict(dict)
            for x in e5['records']:
                if x['circuit']==c and x['method'] in (candidate,'random'):
                    pairs[x['block_id']][x['method']]=x['winner']['metrics']['luts']
            points.append([100*(1-p[candidate]/p['random']) for p in pairs.values() if len(p)==2])
        fig,ax=plt.subplots(figsize=(9,max(3.5,len(cases)*.5+1.5)))
        ax.boxplot(points,vert=False,showmeans=True);ax.set_yticks(range(1,len(cases)+1),cases);ax.axvline(0,color='#667',lw=1)
        ax.set_title('新电路：冻结方法相对随机搜索的逐次结果');ax.set_xlabel('节省的 LUT（%，正数更好）');ax.grid(axis='x',alpha=.2)
        fig.tight_layout();fig.savefig(figures/'e5_distribution.png');plt.close(fig)
        text.append('![新电路分布](figures/e5_distribution.png)\n')
        text.append('箱体覆盖中间一半结果，橙线为中位数，绿色三角为平均值；全部点均保留在统计中。\n')
    text+=['## 如何理解证据\n',
        '- 主指标先在每电路内平均 log(LUT 比值)，再对电路等权平均；区间对实际时段块联合重采样 10,000 次。\n'
        '- 原 10 个电路已经用于诊断，E4 开发结论与新电路评价分开。预算和机制对照属于探索性分析。\n'
        '- 1% 相对 LUT 改善是本项目预设工程目标，不是通用显著性门槛。\n'
        '- 计时受实际机器状态影响；重复和随机运行顺序用于降低影响，无法完全控制后台负载。\n'
        '- 本原型从原始 AIG 重放完整操作序列来评估每个候选，便于准确复现，也会产生重复前缀的开销；没有测试增量执行版束搜索，结论限于当前实现与配置。\n'
        '- CEC 是功能正确性检查，不是统计稳定性证据。固定基准回退保证可行输出，不应被当成算法改善。\n']
    markdown='\n'.join(text);(report/'REPORT.md').write_text(markdown,encoding='utf-8')
    write_json(report/'findings.json',findings)
    # Small, dependency-free Markdown subset matching the generated report.
    rendered=[];in_table=False
    for line in markdown.splitlines():
        if line.startswith('|'):
            if set(line.replace('|','').replace(' ','').replace('-',''))==set():continue
            if not in_table:rendered.append('<div class="scroll"><table>');in_table=True
            rendered.append('<tr>'+''.join('<td>'+html.escape(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>');continue
        if in_table:rendered.append('</table></div>');in_table=False
        if line.startswith('!['):
            url=line.split('](')[1].rstrip(')');rendered.append(f'<img src="{html.escape(url)}" alt="实验图表">')
        elif line.startswith('#'):
            level=len(line)-len(line.lstrip('#'));rendered.append(f'<h{level}>{html.escape(line[level:].strip())}</h{level}>')
        elif line.strip():rendered.append('<p>'+html.escape(line).replace('**','').replace('`','')+'</p>')
    if in_table:rendered.append('</table></div>')
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>LUT 搜索扩展实验</title><style>body{font:16px/1.7 "Microsoft YaHei",sans-serif;color:#213348;background:#f1f5f8;margin:0}main{max-width:1120px;margin:30px auto;background:white;padding:36px;border-radius:12px}h1{font-size:30px}h2{margin-top:36px;border-top:1px solid #dbe3ec;padding-top:24px}table{border-collapse:collapse;width:100%;font-size:14px}td{padding:9px 12px;border-bottom:1px solid #dbe3ec;white-space:nowrap}tr:first-child{background:#e7f1f6;font-weight:bold}img{max-width:100%;height:auto}.scroll{overflow-x:auto}p{margin:12px 0}</style><main>'+''.join(rendered)+'</main></html>'
    (report/'report.html').write_text(page,encoding='utf-8');print(report/'report.html')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('base',type=Path);a=p.parse_args();build(a.base)
