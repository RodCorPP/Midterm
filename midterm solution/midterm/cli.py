"""Entrada única para validar, ejecutar y comparar planes."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .jev import analyze
from .metrics import History
from .model import Plan, PlanError
from .reporting import save_report, write_json
from .scheduler import run
from .transport import LocalTransport, MPITransport, mpi_worker_loop


def parser():
    root = argparse.ArgumentParser(description='Planificador JEV DAG MPI para agentes MCP')
    sub = root.add_subparsers(dest='command', required=True)
    for name in ('analyze', 'run', 'benchmark'):
        p = sub.add_parser(name)
        p.add_argument('plan', help='Ruta al plan JSON')
        p.add_argument('--provider', choices=('heuristic', 'jev'), default='heuristic')
        p.add_argument('--history', default='results/history.sqlite')
        p.add_argument('--no-history', action='store_true')
        p.add_argument('--output', default=f'results/{name}.json')
        if name in ('run', 'benchmark'):
            p.add_argument('--workers', type=int, default=4)
            p.add_argument('--policy', choices=('critical', 'fifo'), default='critical')
        if name == 'run':
            p.add_argument('--backend', choices=('sequential', 'local', 'mpi'), default='local')
    return root


def prepare(args):
    history = None if args.no_history else History(args.history)
    try:
        plan = Plan.load(args.plan)
        dag, analysis = analyze(plan, history, args.provider)
        return plan, dag, analysis, history
    except Exception:
        if history:
            history.close()
        raise


def local_run(plan, dag, analysis, workers, backend, history=None, policy='critical'):
    transport = LocalTransport(workers)
    try:
        return run(plan, dag, transport, analysis, backend, history, policy)
    finally:
        transport.close()


def print_result(report, output):
    print(f"{report['plan']} | {report['backend']} | {report['workers']} workers | {report['makespan_seconds']:.3f} s | {'OK' if report['ok'] else 'ERROR'}")
    print(f'Resultados: {Path(output).resolve()}')


def run_mpi(args):
    try:
        from mpi4py import MPI
    except (ImportError, RuntimeError) as exc:
        raise PlanError('MPI no disponible. Instala mpi4py y MPICH/Open MPI; consulta README.md') from exc
    comm = MPI.COMM_WORLD
    rank = comm.Get_rank()
    history = None
    if rank == 0:
        try:
            if comm.Get_size() < 2:
                raise PlanError('Usa mpiexec -n 5 python -m midterm run ... --backend mpi')
            plan, dag, analysis, history = prepare(args)
            startup = None
        except Exception as exc:
            startup = str(exc)
    else:
        startup = None
    startup = comm.bcast(startup, root=0)
    if startup:
        if rank == 0:
            print(f'Error: {startup}', file=sys.stderr)
        return 2
    if rank:
        try:
            mpi_worker_loop(comm, MPI)
            return comm.bcast(None, root=0)
        except Exception:
            comm.Abort(2)
            return 2
    transport = MPITransport(comm, MPI)
    try:
        report = run(plan, dag, transport, analysis, 'mpi', history, args.policy)
        transport.close()
        save_report(args.output, report)
        print_result(report, args.output)
        code = 0 if report['ok'] else 1
        comm.bcast(code, root=0)
        return code
    except Exception as exc:
        print(f'Error del coordinador MPI: {exc}', file=sys.stderr)
        comm.Abort(2)
        return 2
    finally:
        if history:
            history.close()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'run' and args.backend == 'mpi':
            return run_mpi(args)
        if getattr(args, 'workers', 1) < 1:
            raise PlanError('workers debe ser mayor que cero')
        plan, dag, analysis, history = prepare(args)
        try:
            if args.command == 'analyze':
                write_json(args.output, {'plan': plan.name, 'analysis': analysis, 'dag': dag.to_dict()})
                Path(args.output).with_suffix('.dot').write_text(dag.to_dot(), encoding='utf-8')
                print(f'DAG válido: {len(dag.tasks)} nodos, {len(dag.reasons)} aristas, camino crítico estimado {max(dag.bottom_levels().values()):.3f} s')
                return 0
            if args.command == 'run':
                workers = 1 if args.backend == 'sequential' else args.workers
                report = local_run(plan, dag, analysis, workers, args.backend, history, args.policy)
                save_report(args.output, report)
                print_result(report, args.output)
                return 0 if report['ok'] else 1
            # Misma estimación congelada en ambos ensayos y sin aprendizaje entre ellos.
            sequential = local_run(plan, dag, analysis, 1, 'sequential')
            parallel = local_run(plan, dag, analysis, args.workers, 'local', policy=args.policy)
            out = Path(args.output)
            save_report(out.with_name(out.stem + '_sequential.json'), sequential)
            save_report(out.with_name(out.stem + '_parallel.json'), parallel)
            speedup = sequential['makespan_seconds'] / parallel['makespan_seconds']
            data = {'plan': plan.name, 'backend': 'local', 'workers': args.workers, 'sequential_seconds': sequential['makespan_seconds'], 'parallel_seconds': parallel['makespan_seconds'], 'speedup': speedup, 'efficiency': speedup / args.workers, 'same_results': sequential['results'] == parallel['results'], 'all_succeeded': sequential['ok'] and parallel['ok'], 'estimated_critical_path_seconds': max(dag.bottom_levels().values()), 'note': 'Medición local con procesos de tarea; no representa un clúster MPI. Reejecuta planes con efectos externos solo si son seguros de repetir.'}
            write_json(out, data)
            print(f"Secuencial {data['sequential_seconds']:.3f} s | Local {data['parallel_seconds']:.3f} s | Speedup {speedup:.2f}x | Resultados iguales: {data['same_results']}")
            return 0 if data['same_results'] and data['all_succeeded'] else 1
        finally:
            if history:
                history.close()
    except (PlanError, OSError, ValueError, RuntimeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
