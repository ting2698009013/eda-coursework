"""Explain mechanisms using every preselected circuit, without rerunning synthesis."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import statistics
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lutsearch.report import aggregate, LABELS, table_md


def ranks(values):
    ordered = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + end - 1) / 2 + 1
        for index in ordered[start:end]:
            result[index] = rank
        start = end
    return result


def spearman(x, y):
    if len(x) < 2:
        return None
    a, b = ranks(x), ranks(y)
    am, bm = statistics.mean(a), statistics.mean(b)
    norm = math.sqrt(sum((v-am)**2 for v in a) * sum((v-bm)**2 for v in b))
    return sum((u-am)*(v-bm) for u, v in zip(a, b))/norm if norm else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    spec = json.loads((run/'experiment.json').read_text(encoding='utf-8'))['identity']['spec']
    completion = json.loads((run/'completion.json').read_text(encoding='utf-8'))
    if not completion['all_valid']:
        raise ValueError('Analysis requires a completed, verified experiment')
    trials = []
    for file in sorted((run/'trials').glob('*/summary.json')):
        trial = json.loads(file.read_text(encoding='utf-8'))
        trial['_path'] = file.parent
        trials.append(trial)
    evidence = []
    for circuit in spec['circuits']:
        candidates = {}
        selected = [t for t in trials if t['circuit'] == circuit and t['config']['seconds'] is None]
        limit = selected[0]['depth_limit']
        for t in selected:
            for line in (t['_path']/'trace.jsonl').read_text(encoding='utf-8').splitlines():
                entry = json.loads(line)
                if entry['status'] != 'ok' or entry['metrics']['luts'] is None:
                    continue
                candidates.setdefault(entry['structure_sha256'], {
                    'trial': t['_path'].name, 'call': entry['call'], 'sequence': entry['sequence'],
                    'metrics': entry['metrics'], 'structure_sha256': entry['structure_sha256']})
        values = list(candidates.values())
        corr = spearman([c['metrics']['and_nodes'] for c in values],
                        [c['metrics']['luts'] for c in values])
        feasible = [c for c in values if c['metrics']['lut_levels'] <= limit]
        # Find a simple, reproducible counterexample; never use it to select the test set.
        pair = None
        for first in sorted(feasible, key=lambda c: (c['metrics']['and_nodes'], c['structure_sha256'])):
            second = next((c for c in feasible if c['metrics']['and_nodes'] > first['metrics']['and_nodes']
                           and c['metrics']['luts'] < first['metrics']['luts']), None)
            if second:
                pair = {'smaller_aig': first, 'fewer_luts': second}
                break
        evidence.append({'circuit': circuit, 'unique_mapped_candidates': len(values),
                         'spearman_aig_lut': corr, 'depth_limit': limit, 'counterexample': pair})
    text = ['# 实验结果怎样解释',
            '本文件由 `scripts/analyze_results.py` 从完整实验日志生成。正式实验名单和算法参数没有按结果调整。',
            '## AIG 节点数能否代表 LUT 数',
            '下表使用调用次数协议中所有方法、所有种子实际评估过的候选，按电路结构去重。相关系数为 Spearman 秩相关；候选没有变化时记为不适用。这是搜索采样中的关系，不能外推为所有可能电路结构的总体规律。',
            table_md(['电路', '不同结构候选', 'AIG–LUT 秩相关', '存在反例'], [
                [d['circuit'], d['unique_mapped_candidates'],
                 '不适用' if d['spearman_aig_lut'] is None else f'{d["spearman_aig_lut"]:.3f}',
                 '是' if d['counterexample'] else '本次未观察到'] for d in evidence])]
    hardware = run/'hardware.json'
    if hardware.exists():
        machine = json.loads(hardware.read_text(encoding='utf-8'))
        text.insert(2, f'实验机器：{machine["cpu_model"]}；WSL 可见 {machine["logical_cpus"]} 个逻辑 CPU、{machine["wsl_memory_gib"]} GiB 内存。ABC 调用串行执行，宿主机后台负载未严格控制。')
    observed = sum(d['counterexample'] is not None for d in evidence)
    text.append(f'{observed}/{len(evidence)} 个电路中观察到：AIG 节点更少的候选，LUT 数反而更多。反例中的两个候选均满足该电路的 LUT 深度约束。完整索引保存在 `analysis.json`。')
    example = next((d for d in evidence if d['counterexample']), None)
    if example:
        pair = example['counterexample']
        text += [f'### 一个具体例子：{example["circuit"]}',
                 table_md(['候选', 'AIG 节点', 'LUT 数', 'LUT 层数', '操作顺序'], [
                     [name, c['metrics']['and_nodes'], c['metrics']['luts'], c['metrics']['lut_levels'],
                      ' → '.join(c['sequence'])] for name, c in [('AIG 更小', pair['smaller_aig']), ('LUT 更少', pair['fewer_luts'])]]),
                 '这个例子说明 AIG 节点数不能保证 LUT 排名正确；它本身不能证明直接使用 LUT 反馈更高效，还必须看下面的相同预算比较。']
    text += ['## 探索分支是否有效']
    comparisons = {}
    for protocol in spec['protocols']:
        ag = aggregate(trials, spec['circuits'], spec['methods'], protocol['name'])
        by_method = {d['method']: d for d in ag}
        ordinary, diverse = by_method['lut_beam'], by_method['lut_beam_diverse']
        wins = sum(diverse['median_luts'][c] < ordinary['median_luts'][c] for c in spec['circuits'])
        losses = sum(diverse['median_luts'][c] > ordinary['median_luts'][c] for c in spec['circuits'])
        ties = len(spec['circuits']) - wins - losses
        comparisons[protocol['name']] = {'wins': wins, 'ties': ties, 'losses': losses,
            'ordinary_gain': ordinary['geomean_gain'], 'exploration_gain': diverse['geomean_gain']}
        text.append(f'{protocol["name"]}：按各电路三个种子的中位数，探索分支相对普通 LUT 束搜索为 {wins} 胜、{ties} 平、{losses} 负；几何平均 LUT 降幅分别为 {diverse["geomean_gain"]:.2f}% 和 {ordinary["geomean_gain"]:.2f}%。')
    text += ['只有三个种子且测试集较小，这些差异应作为本次实验观察，不能宣称统计显著或普遍有效。',
             '## 与更长固定脚本比较']
    stronger = []
    for c in spec['circuits']:
        records = json.loads((run/'references'/c/'reference.json').read_text(encoding='utf-8'))['records']
        b, s = records['resyn2']['metrics'], records['resyn2x2']['metrics']
        u = records['unoptimized']['metrics']
        stronger.append([c, u['luts'], u['lut_levels'], b['luts'], s['luts'], '满足' if s['lut_levels'] <= b['lut_levels'] else '超出'])
    text += [table_md(['电路', '直接映射 LUT', '直接映射层数', 'resyn2 LUT', '两遍 resyn2 LUT', '两遍脚本是否满足主深度约束'], stronger),
             '直接映射没有执行优化序列，但仍调用相同的 6-LUT 映射器。固定脚本也可能使某个电路的 LUT 数增加。因此“相对 resyn2 改善”不一定表示“优于原电路直接映射”，应结合这一列判断。',
             '两遍 resyn2 共 20 步，搜索序列最多 10 步，因此它是额外参照。若它已经达到相似结果，就应讨论搜索成本是否值得，不能只选择较弱的参照来展示收益。',
             '## 这些结果支持什么',
             '本项目完成了优化序列搜索、映射反馈、预算控制、公开电路对比和形式等价验证。最终结论应以完整报告中各方法的收益与成本为依据：既分析有改善的电路，也保留没有改善的电路；将“功能正确”“比固定脚本更好”和“比其他搜索更好”作为三个分别需要证据的判断。']
    out = run/'report'; out.mkdir(exist_ok=True)
    (out/'ANALYSIS.md').write_text('\n\n'.join(text)+'\n', encoding='utf-8')
    (out/'analysis.json').write_text(json.dumps({'candidate_evidence': evidence, 'exploration_comparison': comparisons}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(out/'ANALYSIS.md')


if __name__ == '__main__':
    main()
