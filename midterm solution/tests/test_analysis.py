import json
import os
import tempfile
import unittest
from unittest.mock import patch

from midterm.jev import JevClient, analyze
from midterm.metrics import History
from midterm.model import Plan, PlanError
from tests.test_model_graph import plan, task


class AnalysisTests(unittest.TestCase):
    def test_reference_adds_dependency_without_listing_it(self):
        dag, _ = analyze(plan(task('b', arguments={'x': {'$ref': 'a.result'}}), task('a')))
        self.assertEqual(dag.parents['b'], {'a'})
        self.assertIn('result_reference', dag.reasons['a', 'b'])

    def test_artifact_and_semantic_mentions(self):
        dag, _ = analyze(plan(task('a', produces=['datos_limpios']), task('b', description='Analizar DATOS_LIMPIOS'), task('c', consumes=['datos_limpios'])))
        self.assertEqual(dag.parents['b'], {'a'})
        self.assertEqual(dag.parents['c'], {'a'})

    def test_missing_and_duplicate_artifacts_rejected(self):
        for p in (plan(task('a', consumes=['absent'])), plan(task('a', produces=['x']), task('b', produces=['x']))):
            with self.assertRaises(PlanError):
                analyze(p)

    def test_initial_artifact_needs_no_producer(self):
        p = Plan.from_dict({'name': 'test', 'initial_artifacts': ['existing'], 'tasks': [task('a', consumes=['existing'])]})
        dag, _ = analyze(p)
        self.assertFalse(dag.parents['a'])

    def test_read_read_can_run_together_write_is_serialized(self):
        dag, _ = analyze(plan(task('a', resources={'db': 'read'}), task('b', resources={'db': 'read'}), task('c', resources={'db': 'write'})))
        self.assertFalse(dag.parents['b'])
        self.assertEqual(dag.parents['c'], {'a', 'b'})

    def test_resource_order_respects_existing_reverse_dependency(self):
        dag, _ = analyze(plan(task('b', depends_on=['a'], resources={'db': 'write'}), task('a', resources={'db': 'read'})))
        self.assertEqual(dag.topological(), ['a', 'b'])

    def test_history_updates_only_on_success_and_explicit_weight_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            history = History(temp + '/history.sqlite')
            try:
                history.record('builtin:demo.echo', 2, 4, 'failed')
                self.assertIsNone(history.estimate('builtin:demo.echo'))
                history.record('builtin:demo.echo', 2, 4, 'succeeded')
                history.record('builtin:demo.echo', 4, 2, 'succeeded')
                self.assertAlmostEqual(history.estimate('builtin:demo.echo'), 3.4)
                dag, audit = analyze(plan(task('a'), task('b', estimated_seconds=9)), history)
                self.assertEqual(dag.weights, {'a': 3.4, 'b': 9})
                self.assertEqual(audit['weight_sources']['a'], 'history_ewma')
            finally:
                history.close()


class FakeJev(JevClient):
    def __init__(self, choice='a_before_b', probability=0.95):
        super().__init__()
        self.choice, self.probability = choice, probability

    def decide(self, state, questions):
        return {key: {'choice': self.choice if key.startswith('dep') else 'short', 'probabilities': {self.choice if key.startswith('dep') else 'short': self.probability}} for key in questions}


class JevTests(unittest.TestCase):
    def test_remote_decisions_add_edges_and_costs(self):
        dag, audit = analyze(plan(task('a'), task('b')), provider='jev', client=FakeJev())
        self.assertEqual(dag.parents['b'], {'a'})
        self.assertEqual(dag.weights['a'], 0.2)
        self.assertEqual(audit['provider'], 'jev')

    def test_uncertain_dependency_blocks_execution(self):
        with self.assertRaisesRegex(PlanError, 'ambigua'):
            analyze(plan(task('a'), task('b')), provider='jev', client=FakeJev(probability=0.5))

    def test_remote_cycle_never_overrides_explicit_dependency(self):
        with self.assertRaisesRegex(PlanError, 'Ciclo'):
            analyze(plan(task('a', depends_on=['b']), task('b')), provider='jev', client=FakeJev())

    def test_missing_credentials_has_clear_error(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(PlanError, 'JEV_API_KEY'):
            JevClient().decide({}, {'question': {}})

    def test_native_http_contract(self):
        import io
        response = io.BytesIO(json.dumps({'model': 'jev-latest', 'answers': {'q': {'choice': 'yes'}}}).encode())
        with patch.dict(os.environ, {'JEV_API_KEY': 'test-key'}), patch('midterm.jev.urlopen', return_value=response) as http:
            self.assertEqual(JevClient().decide({'task': 'x'}, {'q': {'type': 'choice'}})['q']['choice'], 'yes')
            request = http.call_args.args[0]
            self.assertEqual(request.headers['Authorization'], 'Bearer test-key')
            self.assertEqual(request.full_url, 'https://api.typesafe.ai/v1/systemone')
            self.assertEqual(json.loads(request.data)['model'], 'jev-latest')
            self.assertEqual(json.loads(request.data)['questions']['q']['type'], 'choice')

    def test_incomplete_remote_response_is_rejected(self):
        import io
        response = io.BytesIO(b'{"model":"jev-latest","answers":{}}')
        with patch.dict(os.environ, {'JEV_API_KEY': 'test-key'}), patch('midterm.jev.urlopen', return_value=response), self.assertRaises(PlanError):
            JevClient().decide({}, {'q': {}})
