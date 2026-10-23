import unittest

from midterm.graph import DAG
from midterm.model import Plan, PlanError, references, resolve


def plan(*tasks):
    return Plan.from_dict({'name': 'test', 'tasks': list(tasks)})


def task(id, **kwargs):
    return {'id': id, 'tool': 'demo.echo', **kwargs}


class ModelTests(unittest.TestCase):
    def test_rejects_empty_plan(self):
        with self.assertRaises(PlanError):
            plan()

    def test_rejects_duplicate_ids(self):
        with self.assertRaises(PlanError):
            plan(task('a'), task('a'))

    def test_rejects_unknown_fields(self):
        with self.assertRaises(PlanError):
            plan(task('a', depends_onn=['b']))

    def test_rejects_missing_dependency_and_self_dependency(self):
        for parent in ('absent', 'a'):
            with self.subTest(parent=parent), self.assertRaises(PlanError):
                plan(task('a', depends_on=[parent]))

    def test_rejects_nonfinite_or_boolean_weights(self):
        for weight in (float('nan'), float('inf'), -1, 0, True, '1'):
            with self.subTest(weight=weight), self.assertRaises(PlanError):
                plan(task('a', estimated_seconds=weight))

    def test_retry_requires_idempotence(self):
        with self.assertRaises(PlanError):
            plan(task('a', retries=1))
        self.assertEqual(plan(task('a', retries=1, idempotent=True)).tasks[0].retries, 1)

    def test_rejects_unknown_mcp_server(self):
        with self.assertRaises(PlanError):
            plan(task('a', server='missing'))

    def test_nested_references_preserve_types(self):
        args = {'nested': [{'$ref': 'a.result.rows.1.value'}], 'object': {'$ref': 'a.result'}}
        data = {'a': {'rows': [{'value': 1}, {'value': 42}]}}
        self.assertEqual(references(args), {'a'})
        self.assertEqual(resolve(args, data), {'nested': [42], 'object': data['a']})

    def test_bad_reference_does_not_execute_code(self):
        with self.assertRaises(PlanError):
            references({'$ref': 'a.__class__'})
        with self.assertRaises(PlanError):
            references({'$ref': 'a.result', 'extra': 'bad'})
        with self.assertRaises(PlanError):
            resolve({'$ref': 'a.result.absent'}, {'a': {}})


class GraphTests(unittest.TestCase):
    def test_topological_order_with_reversed_input(self):
        dag = DAG(plan(task('c'), task('b'), task('a')))
        dag.add('a', 'b', 'test')
        dag.add('b', 'c', 'test')
        self.assertEqual(dag.topological(), ['a', 'b', 'c'])

    def test_cycle_rejected(self):
        dag = DAG(plan(task('a'), task('b')))
        dag.add('a', 'b', 'test')
        dag.add('b', 'a', 'test')
        with self.assertRaisesRegex(PlanError, 'Ciclo'):
            dag.topological()

    def test_bottom_level_and_duplicate_edges(self):
        dag = DAG(plan(task('a', estimated_seconds=1), task('b', estimated_seconds=3), task('c', estimated_seconds=2)))
        dag.add('a', 'b', 'explicit')
        dag.add('a', 'b', 'reference')
        dag.add('a', 'c', 'explicit')
        self.assertEqual(dag.bottom_levels(), {'c': 2, 'b': 3, 'a': 4})
        self.assertEqual(len(dag.parents['b']), 1)
        self.assertEqual(len(dag.reasons['a', 'b']), 2)
