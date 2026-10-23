"""Ensayos reales MPI con igualdad de resultados y medianas por cantidad de workers."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from midterm.reporting import write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', default='examples/analysis_plan.json')
    p.add_argument('--workers', type=int, nargs='+', default=[1, 2, 4])
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--launcher', default=shutil.which('mpiexec') or str(Path(sys.executable).parent / 'mpiexec'))
    p.add_argument('--output', default='results/scaling.json')
    args = p.parse_args()
    if args.repeats < 1 or any(w < 1 for w in args.workers):
        p.error('repeats y workers deben ser positivos')
    out = (ROOT / args.output).resolve()
    records, expected = [], None
    cases = [('sequential', 1)] + [('mpi', w) for w in args.workers]
    # Intercalar casos reduce el sesgo de medir siempre primero toda una configuración.
    for repeat in range(1, args.repeats + 1):
        for backend, workers in cases:
            target = out.parent / f'{out.stem}_{backend}_{workers}_trial{repeat}.json'
            command = [sys.executable, '-m', 'midterm', 'run', args.plan, '--backend', backend, '--no-history', '--output', str(target)]
            if backend == 'mpi':
                command = [args.launcher, '-n', str(workers + 1)] + command
            completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=180)
            if completed.returncode:
                raise RuntimeError(f'Falló {backend}/{workers}: {completed.stderr[-2000:]}')
            report = json.loads(target.read_text())
            if not report['ok']:
                raise RuntimeError('El ensayo contiene tareas fallidas')
            if expected is None:
                expected = report['results']
            if report['results'] != expected:
                raise RuntimeError('Los resultados cambian entre ensayos')
            record = {'backend': backend, 'workers': workers, 'trial': repeat, 'makespan_seconds': report['makespan_seconds']}
            records.append(record)
            print(f"Ensayo {repeat}: {backend}, {workers} workers, {record['makespan_seconds']:.3f} s", flush=True)
    baseline = statistics.median(r['makespan_seconds'] for r in records if r['backend'] == 'sequential')
    summary = []
    for backend, workers in cases:
        values = [r['makespan_seconds'] for r in records if r['backend'] == backend and r['workers'] == workers]
        median = statistics.median(values)
        summary.append({'backend': backend, 'workers': workers, 'median_seconds': median, 'min_seconds': min(values), 'max_seconds': max(values), 'speedup': baseline / median, 'efficiency': baseline / median / workers})
    write_json(out, {'plan': args.plan, 'repeats': args.repeats, 'same_results': True, 'scope': 'Varios procesos en una sola computadora; latencia sintética, sin costo de análisis ni arranque MPI', 'summary': summary, 'trials': records})
    with out.with_suffix('.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    print(f'Resumen: {out}')


if __name__ == '__main__':
    main()
