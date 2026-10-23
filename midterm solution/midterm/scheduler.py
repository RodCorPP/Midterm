"""List scheduling dinámico con indegrees, camino crítico y propagación de fallos."""
from __future__ import annotations

import heapq
import time

from .graph import DAG
from .metrics import History
from .model import Plan, resolve


def run(plan: Plan, dag: DAG, transport, analysis: dict, backend: str, history: History | None = None, policy: str = 'critical') -> dict:
    if policy not in ('critical', 'fifo'):
        raise ValueError(f'Política desconocida: {policy}')
    started = time.perf_counter()
    levels = dag.bottom_levels()
    indegree = {t: len(p) for t, p in dag.parents.items()}
    states = {t: 'pending' for t in dag.tasks}
    results, errors, attempts, events = {}, {}, {t: 0 for t in dag.tasks}, []
    ready, available, active = [], list(transport.workers), {}
    queue_sequence = 0
    heapq.heapify(available)

    def enqueue(t):
        nonlocal queue_sequence
        queue_sequence += 1
        key = (-levels[t], -dag.tasks[t].priority, dag.order[t]) if policy == 'critical' else (queue_sequence, 0, dag.order[t])
        heapq.heappush(ready, (*key, t))

    def fail(t, error):
        states[t] = 'failed'
        errors[t] = error
        stack = list(dag.children[t])
        while stack:
            child = stack.pop()
            if states[child] == 'pending':
                states[child] = 'skipped'
                errors[child] = f'Dependencia fallida: {t}'
                stack.extend(dag.children[child])

    for t, count in indegree.items():
        if not count:
            enqueue(t)
    while ready or active:
        while ready and available:
            *_, t = heapq.heappop(ready)
            if states[t] != 'pending':
                continue
            task = dag.tasks[t]
            try:
                arguments = resolve(task.arguments, results)
            except ValueError as exc:
                fail(t, str(exc))
                continue
            worker = heapq.heappop(available)
            states[t] = 'running'
            attempts[t] += 1
            active[worker] = (t, time.perf_counter() - started)
            transport.submit(worker, task, arguments, plan.servers.get(task.server))
        if not active:
            continue
        worker, response = transport.receive()
        t, dispatch = active.pop(worker)
        ended = time.perf_counter() - started
        heapq.heappush(available, worker)
        status = response['status']
        duration = response['duration_seconds']
        event = {'task': t, 'worker': worker, 'attempt': attempts[t], 'start_seconds': dispatch, 'end_seconds': ended, 'duration_seconds': duration, 'status': status, 'predicted_seconds': dag.weights[t]}
        if 'error' in response:
            event['error'] = response['error']
        for key in ('peak_rss_bytes', 'cpu_seconds'):
            if key in response:
                event[key] = response[key]
        events.append(event)
        if history:
            history.record(dag.tasks[t].metric_key, dag.weights[t], duration, status)
        if status == 'succeeded':
            states[t] = 'succeeded'
            results[t] = response.get('result')
            for child in sorted(dag.children[t], key=dag.order.get):
                indegree[child] -= 1
                if indegree[child] == 0 and states[child] == 'pending':
                    enqueue(child)
        elif attempts[t] <= dag.tasks[t].retries:
            states[t] = 'pending'
            enqueue(t)
        else:
            fail(t, response.get('error', 'Error de ejecución'))
    elapsed = time.perf_counter() - started
    if any(s in ('running', 'pending') for s in states.values()):
        raise RuntimeError('El scheduler terminó con tareas pendientes')
    total_duration = sum(e['duration_seconds'] for e in events)
    return {
        'plan': plan.name, 'backend': backend, 'policy': policy, 'workers': len(transport.workers),
        'ok': all(s == 'succeeded' for s in states.values()),
        'makespan_seconds': elapsed, 'worker_busy_seconds': total_duration,
        'utilization': total_duration / (elapsed * len(transport.workers)) if elapsed else 0,
        'states': states, 'attempts': attempts, 'errors': errors, 'results': results,
        'events': events, 'analysis': analysis, 'dag': dag.to_dict(),
        'resource_metrics': {
            'max_task_peak_rss_bytes': max((e.get('peak_rss_bytes', 0) for e in events), default=0),
            'total_task_cpu_seconds': sum(e.get('cpu_seconds', 0) for e in events),
            'note': 'RSS máximo individual de procesos de tarea; excluye memoria agregada del job y servidores MCP hijos'
        },
    }
