"""Aislamiento por tarea: timeout real del proceso y sus hijos en POSIX."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from .model import Task


def execute(task: Task, arguments: dict, server: dict | None = None) -> dict:
    started = time.perf_counter()
    payload = json.dumps({'tool': task.tool, 'arguments': arguments, 'server': server, 'timeout_seconds': task.timeout_seconds}, allow_nan=False)
    process = subprocess.Popen([sys.executable, '-m', 'midterm.worker'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=os.name == 'posix')
    try:
        # MCP necesita cerrar su sesión y el grupo independiente del servidor.
        out, _ = process.communicate(payload, timeout=task.timeout_seconds + (5 if server else 0))
        if process.returncode:
            result = {'status': 'failed', 'error': f'El proceso de tarea terminó con código {process.returncode}'}
        else:
            result = json.loads(out)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.communicate()
        result = {'status': 'failed', 'error': f'Timeout después de {task.timeout_seconds:g} s'}
    except (json.JSONDecodeError, ValueError) as exc:
        result = {'status': 'failed', 'error': f'Respuesta de worker inválida: {type(exc).__name__}'}
    result['duration_seconds'] = time.perf_counter() - started
    return result


def main():
    try:
        from .tools import invoke
        payload = json.load(sys.stdin)
        result = invoke(payload['tool'], payload['arguments'], payload['server'], payload['timeout_seconds'])
        response = {'status': 'succeeded', 'result': result}
        encoded = json.dumps(response, allow_nan=False)
    except TimeoutError:
        encoded = json.dumps({'status': 'failed', 'error': 'Timeout de la sesión MCP'})
    except Exception as exc:
        message = str(exc)
        for key, value in os.environ.items():
            if any(s in key.upper() for s in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')) and len(value) >= 4:
                message = message.replace(value, '[REDACTED]')
        encoded = json.dumps({'status': 'failed', 'error': f'{type(exc).__name__}: {message[:1000]}'})
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        response = json.loads(encoded)
        response['peak_rss_bytes'] = int(usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024))
        response['cpu_seconds'] = usage.ru_utime + usage.ru_stime
        encoded = json.dumps(response, allow_nan=False)
    except ImportError:
        pass
    print(encoded)


if __name__ == '__main__':
    main()
