"""DAG ponderado y prioridad bottom level (camino restante más costoso)."""
from __future__ import annotations

import heapq
import json
from .model import Plan, PlanError


class DAG:
    def __init__(self, plan: Plan):
        self.tasks = {t.id: t for t in plan.tasks}
        self.order = {t.id: i for i, t in enumerate(plan.tasks)}
        self.parents = {t: set() for t in self.tasks}
        self.children = {t: set() for t in self.tasks}
        self.reasons: dict[tuple[str, str], list[str]] = {}
        self.weights = {t.id: t.estimated_seconds or 0.1 for t in plan.tasks}

    def add(self, source: str, target: str, reason: str):
        if source not in self.tasks or target not in self.tasks or source == target:
            raise PlanError(f"Arista inválida {source} -> {target}")
        self.parents[target].add(source)
        self.children[source].add(target)
        reasons = self.reasons.setdefault((source, target), [])
        if reason not in reasons:
            reasons.append(reason)

    def topological(self) -> list[str]:
        indegree = {t: len(p) for t, p in self.parents.items()}
        ready = [(self.order[t], t) for t, n in indegree.items() if n == 0]
        heapq.heapify(ready)
        result = []
        while ready:
            _, t = heapq.heappop(ready)
            result.append(t)
            for child in self.children[t]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, (self.order[child], child))
        if len(result) != len(self.tasks):
            blocked = sorted(t for t, n in indegree.items() if n)
            raise PlanError(f"Ciclo en las dependencias; nodos bloqueados: {', '.join(blocked)}")
        return result

    def bottom_levels(self) -> dict[str, float]:
        levels: dict[str, float] = {}
        for t in reversed(self.topological()):
            levels[t] = self.weights[t] + max((levels[c] for c in self.children[t]), default=0.0)
        return levels

    def to_dict(self):
        levels = self.bottom_levels()
        return {
            "nodes": [{"id": t, "tool": task.tool, "weight_seconds": self.weights[t], "bottom_level": levels[t]} for t, task in self.tasks.items()],
            "edges": [{"from": a, "to": b, "reasons": reasons} for (a, b), reasons in sorted(self.reasons.items())],
            "topological_order": self.topological(),
            "critical_path_seconds": max(levels.values()),
            "total_work_seconds": sum(self.weights.values()),
        }

    def to_dot(self) -> str:
        lines = ['digraph plan {', '  rankdir=LR;']
        for t in self.tasks:
            label = f"{t}\n{self.weights[t]:.3f} s"
            lines.append(f"  {json.dumps(t)} [label={json.dumps(label)}];")
        for a, b in sorted(self.reasons):
            lines.append(f"  {json.dumps(a)} -> {json.dumps(b)};")
        return '\n'.join(lines + ['}']) + '\n'
