import importlib.util
import tempfile
import unittest
from pathlib import Path

from midterm.cli import local_run
from midterm.jev import analyze
from midterm.model import Plan
from midterm.reporting import save_report
from midterm.scheduler import run
from tests.test_model_graph import plan, task


def execute_plan(p, workers=3):
    dag, analysis = analyze(p)
    return local_run(p, dag, analysis, workers, 'test')


class ExecutionTests(unittest.TestCase):
    def test_results_flow_and_dependencies_are_respected(self):
        p = plan(task('a', arguments={'value': {'numbers': [1, 2, 3]}}), task('b', tool='stats.describe', arguments={'values': {'$ref': 'a.result.numbers'}}))
        report = execute_plan(p)
        self.assertTrue(report['ok'])
        self.assertEqual(report['results']['b']['mean'], 2)
        events = {e['task']: e for e in report['events']}
        self.assertLessEqual(events['a']['end_seconds'], events['b']['start_seconds'])

    def test_independent_tasks_overlap_on_distinct_workers(self):
        report = execute_plan(plan(*(task(t, arguments={'value': t, 'delay_seconds': 0.2}) for t in ('a', 'b', 'c'))))
        self.assertTrue(report['ok'])
        self.assertEqual(len({e['worker'] for e in report['events']}), 3)
        self.assertLess(max(e['start_seconds'] for e in report['events']), min(e['end_seconds'] for e in report['events']))

    def test_heaviest_remaining_branch_starts_first(self):
        p = plan(task('short', estimated_seconds=1), task('critical', estimated_seconds=2), task('tail', estimated_seconds=10, depends_on=['critical']))
        report = execute_plan(p, 1)
        self.assertEqual([e['task'] for e in report['events']], ['critical', 'tail', 'short'])

    def test_fifo_is_a_distinct_baseline(self):
        p = plan(task('first', estimated_seconds=1), task('heavy', estimated_seconds=10))
        dag, audit = analyze(p)
        report = local_run(p, dag, audit, 1, 'test', policy='fifo')
        self.assertEqual([e['task'] for e in report['events']], ['first', 'heavy'])

    def test_failure_skips_descendants_and_preserves_independent_branch(self):
        report = execute_plan(Plan.load('examples/failure_plan.json'))
        self.assertFalse(report['ok'])
        self.assertEqual(report['states'], {'failure': 'failed', 'blocked': 'skipped', 'independent': 'succeeded'})
        self.assertEqual(report['attempts']['failure'], 2)
        self.assertNotIn('blocked', [e['task'] for e in report['events']])

    def test_timeout_terminates_task(self):
        report = execute_plan(plan(task('slow', timeout_seconds=0.15, arguments={'delay_seconds': 5})))
        self.assertFalse(report['ok'])
        self.assertIn('Timeout', report['errors']['slow'])
        self.assertLess(report['makespan_seconds'], 3)

    def test_unresolvable_reference_fails_only_affected_branch(self):
        report = execute_plan(plan(task('a', arguments={'value': {}}), task('b', arguments={'x': {'$ref': 'a.result.missing'}}), task('c', depends_on=['b']), task('d')))
        self.assertEqual(report['states'], {'a': 'succeeded', 'b': 'failed', 'c': 'skipped', 'd': 'succeeded'})

    def test_report_escapes_external_text(self):
        report = execute_plan(plan(task('a', arguments={'value': '<script>alert(1)</script>'})))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'report.json'
            save_report(path, report)
            rendered = path.with_suffix('.html').read_text()
            self.assertNotIn('<script>', rendered)
            self.assertIn('&lt;script&gt;', rendered)

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'Dependencia MCP opcional no instalada')
    def test_real_mcp_stdio_roundtrip(self):
        report = execute_plan(Plan.load('examples/mcp_plan.json'))
        self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(report['results']['statistics']['count'], 82)
        self.assertIn('markdown', report['results']['summary'])

    @unittest.skipUnless(importlib.util.find_spec('mcp'), 'Dependencia MCP opcional no instalada')
    def test_real_mcp_timeout_closes_session(self):
        p = Plan.from_dict({'name': 'timeout MCP', 'servers': {'analytics': {'command': '@python', 'args': ['-m', 'midterm.mcp_server']}}, 'tasks': [task('slow', server='analytics', tool='describe', arguments={'values': [1, 2, 3], 'delay_seconds': 10}, timeout_seconds=0.8)]})
        report = execute_plan(p)
        self.assertFalse(report['ok'])
        self.assertLess(report['makespan_seconds'], 7)
        self.assertIn('Timeout', report['errors']['slow'])


class RetryTransport:
    workers = [1]

    def __init__(self):
        self.calls = 0

    def submit(self, worker, task, args, server):
        self.calls += 1

    def receive(self):
        if self.calls == 1:
            return 1, {'status': 'failed', 'error': 'temporal', 'duration_seconds': 0.01}
        return 1, {'status': 'succeeded', 'result': 42, 'duration_seconds': 0.01}


class RetryTests(unittest.TestCase):
    def test_retry_releases_successor_only_after_success(self):
        p = plan(task('a', retries=1, idempotent=True), task('b', depends_on=['a']))
        dag, audit = analyze(p)
        report = run(p, dag, RetryTransport(), audit, 'test')
        self.assertTrue(report['ok'])
        self.assertEqual([e['task'] for e in report['events']], ['a', 'a', 'b'])
