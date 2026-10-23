"""Comprueba precedencias, exclusión por worker e igualdad de resultados guardados."""
import argparse
import json
from pathlib import Path


def verify(report):
    assert report['ok'], report['errors']
    events = report['events']
    finished = {e['task']: e['end_seconds'] for e in events if e['status'] == 'succeeded'}
    starts = {}
    for event in events:
        starts[event['task']] = min(starts.get(event['task'], float('inf')), event['start_seconds'])
        assert event['end_seconds'] >= event['start_seconds']
        assert event['duration_seconds'] >= 0
    for edge in report['dag']['edges']:
        assert finished[edge['from']] <= starts[edge['to']], edge
    for worker in range(1, report['workers'] + 1):
        assigned = sorted((e for e in events if e['worker'] == worker), key=lambda e: e['start_seconds'])
        for before, after in zip(assigned, assigned[1:]):
            assert before['end_seconds'] <= after['start_seconds'], f'Worker {worker} recibió tareas simultáneas'
    return report['results']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+')
    parser.add_argument('--same-results', action='store_true')
    args = parser.parse_args()
    expected = None
    for path in args.reports:
        report = json.loads(Path(path).read_text())
        results = verify(report)
        if expected is not None and args.same_results:
            assert results == expected, f'Resultados distintos: {path}'
        expected = results
        print(f'Verificado: {path}')


if __name__ == '__main__':
    main()
