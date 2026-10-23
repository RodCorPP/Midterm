"""Contrato JSON estricto y referencias tipadas a resultados de otras tareas."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PlanError(ValueError):
    pass


def positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise PlanError(f"{label} debe ser un número finito mayor que cero")
    return float(value)


def strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise PlanError(f"{label} debe ser una lista de cadenas no vacías")
    if len(value) != len(set(value)):
        raise PlanError(f"{label} contiene duplicados")
    return value


def references(value: Any) -> set[str]:
    if isinstance(value, dict):
        if "$ref" in value:
            if len(value) != 1 or not isinstance(value["$ref"], str):
                raise PlanError("Una referencia solo admite {$ref: 'tarea.result.ruta'}")
            parts = value["$ref"].split(".")
            if len(parts) < 2 or parts[1] != "result" or any(not p for p in parts):
                raise PlanError(f"Referencia inválida: {value['$ref']}")
            return {parts[0]}
        return set().union(*(references(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(references(v) for v in value)) if value else set()
    return set()


def resolve(value: Any, results: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if "$ref" in value:
            parts = value["$ref"].split(".")
            try:
                current = results[parts[0]]
                for part in parts[2:]:
                    current = current[int(part)] if isinstance(current, list) else current[part]
                return current
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise PlanError(f"No se puede resolver {value['$ref']}") from exc
        return {k: resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, results) for v in value]
    return value


@dataclass
class Task:
    id: str
    tool: str
    description: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    consumes: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    resources: dict[str, str] = field(default_factory=dict)
    server: str | None = None
    estimated_seconds: float | None = None
    priority: int = 0
    timeout_seconds: float = 30.0
    retries: int = 0
    idempotent: bool = False

    @property
    def metric_key(self) -> str:
        return f"{self.server or 'builtin'}:{self.tool}"


@dataclass
class Plan:
    name: str
    tasks: list[Task]
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    initial_artifacts: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Plan:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=lambda s: (_ for _ in ()).throw(PlanError(f"Número inválido: {s}")))
        except (OSError, json.JSONDecodeError) as exc:
            raise PlanError(f"No se pudo leer el plan: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Any) -> Plan:
        if not isinstance(data, dict) or set(data) - {"name", "tasks", "servers", "initial_artifacts"}:
            raise PlanError("El plan debe ser un objeto con name, tasks, servers e initial_artifacts")
        if not isinstance(data.get("name"), str) or not data["name"].strip():
            raise PlanError("El plan requiere un nombre")
        if not isinstance(data.get("tasks"), list) or not data["tasks"]:
            raise PlanError("El plan requiere al menos una tarea")
        servers = data.get("servers", {})
        if not isinstance(servers, dict):
            raise PlanError("servers debe ser un objeto")
        for name, config in servers.items():
            if not isinstance(config, dict) or set(config) - {"command", "args", "env", "cwd"}:
                raise PlanError(f"Configuración de servidor inválida: {name}")
            if not isinstance(config.get("command"), str) or not config["command"]:
                raise PlanError(f"Servidor {name}: falta command")
            if not isinstance(config.get("args", []), list) or any(not isinstance(x, str) for x in config.get("args", [])):
                raise PlanError(f"Servidor {name}: args inválido")
            if not isinstance(config.get("env", {}), dict) or any(not isinstance(v, str) for v in config.get("env", {}).values()):
                raise PlanError(f"Servidor {name}: env debe contener nombres de variables de entorno")
            if "cwd" in config and not isinstance(config["cwd"], str):
                raise PlanError(f"Servidor {name}: cwd inválido")
        tasks = []
        allowed = set(Task.__dataclass_fields__)
        for raw in data["tasks"]:
            if not isinstance(raw, dict) or set(raw) - allowed:
                raise PlanError(f"Campos de tarea desconocidos: {raw}")
            if not isinstance(raw.get("id"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", raw["id"]):
                raise PlanError("id debe iniciar con letra y contener letras, números, _ o -")
            if not isinstance(raw.get("tool"), str) or not raw["tool"]:
                raise PlanError(f"{raw['id']}: falta tool")
            t = Task(**raw)
            if not isinstance(t.description, str) or not isinstance(t.arguments, dict):
                raise PlanError(f"{t.id}: description o arguments inválido")
            for name in ("depends_on", "consumes", "produces"):
                strings(getattr(t, name), f"{t.id}.{name}")
            if not isinstance(t.resources, dict) or any(not isinstance(k, str) or not k or v not in ("read", "write") for k, v in t.resources.items()):
                raise PlanError(f"{t.id}: resources requiere valores read o write")
            if t.estimated_seconds is not None:
                t.estimated_seconds = positive(t.estimated_seconds, f"{t.id}.estimated_seconds")
            t.timeout_seconds = positive(t.timeout_seconds, f"{t.id}.timeout_seconds")
            if type(t.priority) is not int or type(t.retries) is not int or not 0 <= t.retries <= 10 or type(t.idempotent) is not bool:
                raise PlanError(f"{t.id}: priority, retries o idempotent inválido")
            if t.retries and not t.idempotent:
                raise PlanError(f"{t.id}: los reintentos requieren idempotent=true")
            if t.server is not None and (not isinstance(t.server, str) or t.server not in servers):
                raise PlanError(f"{t.id}: servidor desconocido")
            references(t.arguments)
            tasks.append(t)
        ids = {t.id for t in tasks}
        if len(ids) != len(tasks):
            raise PlanError("Hay identificadores de tarea duplicados")
        for t in tasks:
            dependencies = set(t.depends_on) | references(t.arguments)
            if dependencies - ids or t.id in dependencies:
                raise PlanError(f"{t.id}: dependencia desconocida o autorreferencia: {sorted(dependencies)}")
        initial = strings(data.get("initial_artifacts", []), "initial_artifacts")
        return cls(data["name"], tasks, servers, initial)
