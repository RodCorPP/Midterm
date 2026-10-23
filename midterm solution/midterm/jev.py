"""Dependencias verificables, conflictos y adaptador opcional al modelo Jev."""
from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .graph import DAG
from .metrics import History
from .model import Plan, PlanError, references


def normalized(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c))


class JevClient:
    """API oficial System One: preguntas choice y probabilidades."""
    def __init__(self, endpoint: str = 'https://api.typesafe.ai/v1/systemone', threshold: float = 0.75):
        self.endpoint = endpoint
        self.threshold = threshold

    def decide(self, state: dict, questions: dict) -> dict:
        key = os.environ.get('TYPESAFE_API_KEY') or os.environ.get('JEV_API_KEY')
        if not key:
            raise PlanError('El proveedor jev requiere TYPESAFE_API_KEY (o JEV_API_KEY) en el entorno')
        body = json.dumps({'model': os.environ.get('JEV_MODEL', 'jev-latest'), 'state': state, 'questions': questions}, allow_nan=False).encode()
        if len(body) > 32 * 1024 * 1024:
            raise PlanError('La petición Jev supera 32 MiB; divide el plan')
        request = Request(self.endpoint, data=body, headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            # No registrar cuerpo HTTP, cabeceras ni credenciales.
            raise PlanError(f'No se pudo consultar Jev ({type(exc).__name__})') from exc
        if not isinstance(payload, dict):
            raise PlanError('Jev devolvió una respuesta inválida')
        answers = payload.get('answers')
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise PlanError('La respuesta Jev no contiene todas las preguntas solicitadas')
        return answers

    def analyze(self, plan: Plan) -> tuple[list[tuple[str, str, str]], dict[str, float], list[dict]]:
        state = {'tasks': [{'id': t.id, 'description': t.description, 'tool': t.tool, 'consumes': t.consumes, 'produces': t.produces, 'depends_on': sorted(set(t.depends_on) | references(t.arguments)), 'resources': t.resources} for t in plan.tasks]}
        questions, definitions = {}, {}
        for i, a in enumerate(plan.tasks):
            for b in plan.tasks[i + 1:]:
                key = f'dep_{len(questions)}'
                questions[key] = {'type': 'choice', 'instructions': f'What execution dependency is necessary between {a.id} and {b.id}? Consider produced data and task meaning, not their listing order.', 'criteria': {'a_before_b': f'{b.id} requires the output or completion of {a.id}', 'b_before_a': f'{a.id} requires the output or completion of {b.id}', 'independent': 'Neither task requires the other; shared inputs alone do not imply dependency'}}
                definitions[key] = ('edge', a.id, b.id)
        costs = {'tiny': 0.05, 'short': 0.2, 'medium': 1.0, 'long': 5.0, 'very_long': 30.0}
        for t in plan.tasks:
            key = f'cost_{t.id}'
            questions[key] = {'type': 'choice', 'instructions': f'Estimate elapsed execution cost of {t.id} in seconds on an ordinary worker, excluding dependencies.', 'criteria': {k: f'About {v} seconds' for k, v in costs.items()}}
            definitions[key] = ('cost', t.id)
        answers = {}
        items = list(questions.items())
        for start in range(0, len(items), 24):
            answers.update(self.decide(state, dict(items[start:start + 24])))
        edges, weights, audit = [], {}, []
        for key, definition in definitions.items():
            answer = answers[key]
            if not isinstance(answer, dict):
                raise PlanError(f'Respuesta Jev inválida: {key}')
            choice = answer.get('choice')
            probabilities = answer.get('probabilities', {})
            if choice not in questions[key]['criteria'] or not isinstance(probabilities, dict):
                raise PlanError(f'Opción Jev inválida: {key}')
            probability = probabilities.get(choice)
            if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1:
                raise PlanError(f'Probabilidad Jev inválida: {key}')
            audit.append({'question': key, 'choice': choice, 'probability': probability})
            if definition[0] == 'edge':
                if probability < self.threshold:
                    raise PlanError(f'Dependencia ambigua entre {definition[1]} y {definition[2]}; declara depends_on o precisa las descripciones')
                _, a, b = definition
                if choice == 'a_before_b':
                    edges.append((a, b, f'jev p={probability:.3f}'))
                elif choice == 'b_before_a':
                    edges.append((b, a, f'jev p={probability:.3f}'))
            else:
                weights[definition[1]] = costs[choice]
        return edges, weights, audit


def analyze(plan: Plan, history: History | None = None, provider: str = 'heuristic', client: JevClient | None = None) -> tuple[DAG, dict]:
    if provider not in ('heuristic', 'jev'):
        raise PlanError(f'Proveedor desconocido: {provider}')
    dag = DAG(plan)
    producers: dict[str, str] = {}
    for t in plan.tasks:
        for artifact in t.produces:
            if artifact in producers or artifact in plan.initial_artifacts:
                raise PlanError(f'El artefacto {artifact} tiene múltiples orígenes; usa nombres/versiones distintos')
            producers[artifact] = t.id
    for t in plan.tasks:
        for parent in t.depends_on:
            dag.add(parent, t.id, 'explicit')
        for parent in references(t.arguments):
            dag.add(parent, t.id, 'result_reference')
        for artifact in t.consumes:
            if artifact in producers:
                dag.add(producers[artifact], t.id, f'artifact:{artifact}')
            elif artifact not in plan.initial_artifacts:
                raise PlanError(f'{t.id}: no existe productor para {artifact}')
        # Regla semántica acotada y auditable: mención exacta de un artefacto.
        for artifact, parent in producers.items():
            if parent != t.id and re.search(r'(?<!\w)' + re.escape(normalized(artifact)) + r'(?!\w)', normalized(t.description)):
                dag.add(parent, t.id, f'description_mentions:{artifact}')
    jev_weights, audit = {}, []
    if provider == 'jev':
        edges, jev_weights, audit = (client or JevClient()).analyze(plan)
        for a, b, reason in edges:
            dag.add(a, b, reason)
    # Validar antes de ordenar conflictos: respeta dependencias aunque el JSON esté invertido.
    topological = dag.topological()
    for i, a in enumerate(topological):
        for b in topological[i + 1:]:
            shared = set(dag.tasks[a].resources) & set(dag.tasks[b].resources)
            for resource in sorted(shared):
                if 'write' in (dag.tasks[a].resources[resource], dag.tasks[b].resources[resource]):
                    dag.add(a, b, f'resource_conflict:{resource}')
    sources = {}
    for t in plan.tasks:
        learned = history.estimate(t.metric_key) if history else None
        if t.estimated_seconds is not None:
            weight, source = t.estimated_seconds, 'explicit'
        elif learned is not None:
            weight, source = learned, 'history_ewma'
        elif t.id in jev_weights:
            weight, source = jev_weights[t.id], 'jev_cost_bucket'
        elif isinstance(t.arguments.get('delay_seconds'), (int, float)) and not isinstance(t.arguments.get('delay_seconds'), bool) and t.arguments['delay_seconds'] > 0:
            weight, source = float(t.arguments['delay_seconds']) + 0.05, 'demo_delay_plus_overhead'
        else:
            weight, source = 0.1, 'default_heuristic'
        if not math.isfinite(weight) or weight <= 0:
            raise PlanError(f'Peso inválido para {t.id}')
        dag.weights[t.id] = weight
        sources[t.id] = source
    dag.topological()
    return dag, {'provider': provider, 'weight_sources': sources, 'jev_decisions': audit, 'notes': ['Heuristic es una línea base determinista; no invoca al modelo Jev.'] if provider == 'heuristic' else []}
