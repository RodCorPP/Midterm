"""Evaluación controlada de calidad, dos alternativas y escalabilidad local."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from midterm.cli import local_run
from midterm.jev import analyze
from midterm.model import Plan
from midterm.reporting import write_json


def canonical_hash(result):
    return hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def query_plan(case):
    def args(**kwargs):
        return {'values': {'$ref': 'load.result'}, 'delay_seconds': 0.02, **kwargs}
    return Plan.from_dict({'name': case['id'], 'tasks': [
        {'id': 'load', 'tool': 'demo.echo', 'arguments': {'value': case['values']}},
        {'id': 'stats', 'tool': 'stats.describe', 'arguments': args()},
        {'id': 'outliers', 'tool': 'stats.anomalies', 'arguments': args(z_threshold=1.5)},
        {'id': 'histogram', 'tool': 'chart.histogram', 'arguments': args(bins=5)},
        {'id': 'summary', 'tool': 'report.compose', 'arguments': {'statistics': {'$ref': 'stats.result'}, 'anomalies': {'$ref': 'outliers.result'}, 'chart': {'$ref': 'histogram.result.counts'}}}
    ]})


def quality(report, case, tolerance):
    result = report['results']
    fields = [('stats', key) for key in case['statistics']] + [('outliers', 'outliers'), ('histogram', 'counts'), ('summary', 'markdown')]
    complete = sum(task in result and key in result[task] for task, key in fields) / len(fields)
    checks = [math.isclose(result.get('stats', {}).get(k, float('inf')), value, rel_tol=tolerance, abs_tol=tolerance) for k, value in case['statistics'].items()]
    checks += [[x['index'] for x in result.get('outliers', {}).get('outliers', [])] == case['outlier_indices'], result.get('histogram', {}).get('counts') == case['histogram_counts']]
    checks += [result.get('summary', {}).get('statistics') == result.get('stats'), result.get('summary', {}).get('anomalies') == result.get('outliers')]
    return {'accuracy': sum(checks) / len(checks), 'completeness': complete, 'dependency_correctness': dependency_correctness(report), 'digest': canonical_hash(result)}


def dependency_correctness(report):
    if not report['ok']:
        return 0.0
    events = {e['task']: e for e in report['events']}
    edges = report['dag']['edges']
    return sum(events[e['from']]['end_seconds'] <= events[e['to']]['start_seconds'] for e in edges) / len(edges) if edges else 1.0


def priority_plan():
    tasks = [{'id': x, 'tool': 'demo.echo', 'arguments': {'value': x, 'delay_seconds': delay}, 'estimated_seconds': delay + 0.05} for x, delay in [('light_a', .15), ('light_b', .15), ('heavy', .45), ('tail', .45)]]
    tasks[-1]['depends_on'] = ['heavy']
    return Plan.from_dict({'name': 'priority_sensitive', 'tasks': tasks})


def scale_plan(size):
    tasks = [{'id': f't{i}', 'tool': 'demo.echo', 'arguments': {'value': i, 'delay_seconds': .02}, 'estimated_seconds': .07} for i in range(size)]
    tasks.append({'id': 'collect', 'tool': 'demo.echo', 'arguments': {'value': [{'$ref': f't{i}.result'} for i in range(size)]}})
    return Plan.from_dict({'name': f'scale_{size}', 'tasks': tasks})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--sizes', type=int, nargs='+', default=[8, 24])
    p.add_argument('--output', default='results/evaluation.json')
    args = p.parse_args()
    if args.repeats < 2 or any(n < 1 for n in args.sizes):
        p.error('Se requieren al menos dos repeticiones y tamaños positivos')
    gold = json.loads((ROOT / 'experiments/golden.json').read_text())
    configurations = [('sequential', 1, 'fifo'), ('fifo', 2, 'fifo'), ('critical', 2, 'critical')]
    observations, checks = [], []
    workloads = [('quality', query_plan(c), c) for c in gold['cases']] + [('priority', priority_plan(), None)] + [('scalability', scale_plan(n), None) for n in args.sizes]
    expected = {}
    for category, plan, case in workloads:
        dag, audit = analyze(plan)
        for repeat in range(1, args.repeats + 1):
            for method, workers, policy in configurations:
                report = local_run(plan, dag, audit, workers, 'sequential' if workers == 1 else 'local', policy=policy)
                if not report['ok']:
                    raise RuntimeError(report['errors'])
                digest = canonical_hash(report['results'])
                if plan.name not in expected:
                    expected[plan.name] = digest
                if digest != expected[plan.name]:
                    raise RuntimeError(f'Resultados inconsistentes: {plan.name}')
                record = {'category': category, 'workload': plan.name, 'tasks': len(plan.tasks), 'method': method, 'workers': workers, 'repeat': repeat, 'makespan_seconds': report['makespan_seconds'], 'worker_busy_seconds': report['worker_busy_seconds'], 'cpu_seconds': report['resource_metrics']['total_task_cpu_seconds'], 'max_task_rss_bytes': report['resource_metrics']['max_task_peak_rss_bytes'], 'result_sha256': digest}
                observations.append(record)
                if case:
                    score = quality(report, case, gold['tolerance'])
                    checks.append({'workload': plan.name, 'method': method, 'repeat': repeat, **score})
                    if min(score['accuracy'], score['completeness'], score['dependency_correctness']) < 1:
                        raise RuntimeError(f'Referencia no satisfecha: {score}')
        print(f'Verificado: {plan.name}, {len(plan.tasks)} tareas, {args.repeats} repeticiones y tres métodos', flush=True)
    summary = []
    for category, plan, _ in workloads:
        baseline = statistics.median(x['makespan_seconds'] for x in observations if x['workload'] == plan.name and x['method'] == 'sequential')
        for method, workers, _ in configurations:
            runs = [x for x in observations if x['workload'] == plan.name and x['method'] == method]
            median = statistics.median(x['makespan_seconds'] for x in runs)
            summary.append({'category': category, 'workload': plan.name, 'tasks': len(plan.tasks), 'method': method, 'workers': workers, 'median_seconds': median, 'min_seconds': min(x['makespan_seconds'] for x in runs), 'max_seconds': max(x['makespan_seconds'] for x in runs), 'speedup': baseline / median, 'efficiency': baseline / median / workers, 'median_cpu_seconds': statistics.median(x['cpu_seconds'] for x in runs), 'max_task_rss_bytes': max(x['max_task_rss_bytes'] for x in runs)})
    result = {'environment': {'python': platform.python_version(), 'platform': platform.platform(), 'processor': platform.machine()}, 'repeats': args.repeats, 'provider': 'heuristic', 'model': None, 'sampling_parameters': None, 'llm_tokens': 0, 'remote_api_cost_usd': 0, 'cost_note': 'Sin llamadas remotas en esta evaluación; no se mide energía ni costo monetario de hardware.', 'memory_note': 'Máximo RSS individual del subproceso de tarea; no es memoria agregada.', 'quality': {'accuracy': min(x['accuracy'] for x in checks), 'completeness': min(x['completeness'] for x in checks), 'dependency_correctness': min(x['dependency_correctness'] for x in checks), 'consistency': 1.0, 'queries': len(gold['cases']), 'checked_runs': len(checks), 'tolerance': gold['tolerance']}, 'summary': summary, 'quality_checks': checks, 'observations': observations}
    write_json(ROOT / args.output, result)
    print(f'Resultados: {ROOT / args.output}')


if __name__ == '__main__':
    main()
