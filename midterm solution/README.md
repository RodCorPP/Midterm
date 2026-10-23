# Planificador distribuido de ejecución paralela para agentes MCP

Solución ejecutable a la propuesta del documento **Planificador MPI JEV MCP**. Recibe un plan JSON, determina dependencias, construye un DAG ponderado y asigna tareas dinámicamente a workers locales o ranks MPI. Incluye herramientas de análisis de datos, integración MCP real, estimación con historial, pruebas y resultados medidos.

**Entrega de Midterm 1:** `Midterm1_ID.pdf`, con los cuatro autores indicados, Q1–Q5, M1–M4 y referencias. Sustituyan `ID` por sus identificadores. Las fuentes editables están en `report/paper/`. La matriz de cumplimiento está en `docs/CUMPLIMIENTO.md` y las mediciones en `docs/RESULTADOS.md`.

El PDF tiene tres páginas de contenido principal y una de referencias y apéndices. Para recompilarlo con LaTeX instalado: `python scripts/build_report.py --id ID`. El script toma cifras de los resultados guardados y comprueba el máximo de cuatro páginas de cuerpo.

## Empezar aquí

Requiere Python 3.11 o superior. Desde esta carpeta, la demostración local funciona sin instalar paquetes:

```bash
python3 -m midterm analyze examples/analysis_plan.json --no-history
python3 -m midterm run examples/analysis_plan.json --workers 4 --no-history
python3 -m midterm benchmark examples/analysis_plan.json --workers 4 --no-history
python3 -m unittest discover -s tests -v
```

El comando `run` guarda JSON, un reporte HTML con cronograma y los artefactos del ejemplo. Abre `results/run.html` en un navegador. Ya se incluyen reportes verificados en `results/mpi.html`, `results/mcp.html` y `results/benchmark_parallel.html`.

## Instalación para MPI y MCP

Se entrega un entorno `.venv` instalado y probado en esta computadora. Para crear uno en otra máquina:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[all]' mpich
```

También puedes usar `python -m pip install -r requirements-lock.txt` para reproducir las versiones verificadas. Si tu clúster ya tiene MPI, instala `.[all]` y utiliza su launcher; no mezcles dos implementaciones MPI. Las ruedas `mpich` están disponibles para macOS y Linux compatibles. Consulta la [instalación oficial de mpi4py](https://mpi4py.github.io/mpi4py/stable/html/install.html).

En esta carpeta, sin activar el entorno:

```bash
.venv/bin/mpiexec -n 5 .venv/bin/python -m midterm run examples/analysis_plan.json --backend mpi --no-history --output results/mpi.json
.venv/bin/python -m midterm run examples/mcp_plan.json --no-history --output results/mcp.json
.venv/bin/mpiexec -n 4 .venv/bin/python -m midterm run examples/mcp_plan.json --backend mpi --no-history --output results/mpi_mcp.json
```

`-n 5` significa **1 coordinador y 4 workers**. En MPI, `--workers` no cambia el tamaño del job: lo determina `mpiexec -n`. El modo secuencial usa un solo worker y el mismo código de ejecución que el modo paralelo.

## Qué contiene

| Ruta | Contenido |
| --- | --- |
| `midterm/model.py` | Contrato del plan y referencias tipadas a resultados |
| `midterm/jev.py` | Análisis de dependencias, conflictos y cliente Jev |
| `midterm/graph.py` | DAG, orden topológico y cálculo del camino restante |
| `midterm/scheduler.py` | Cola de listos, prioridades, resultados y fallos |
| `midterm/transport.py` | Transportes local y MPI con coordinador rank 0 |
| `midterm/worker.py` | Proceso aislado por tarea y timeout |
| `midterm/tools.py` | Herramientas reproducibles y cliente MCP stdio |
| `midterm/mcp_server.py` | Servidor MCP de ejemplo |
| `midterm/metrics.py` | Historial SQLite y estimador EWMA |
| `examples/` | Plan analítico, conflictos, fallos y MCP real |
| `schema/plan.schema.json` | Esquema JSON del contrato |
| `tests/` | Pruebas de validación, análisis, ejecución y MCP |
| `docs/SOLUCION.md` | Diseño, algoritmo, alcance y trazabilidad de requisitos |
| `docs/RESULTADOS.md` | Evidencia de verificación y mediciones |
| `scripts/benchmark_mpi.py` | Comparación repetida secuencial contra MPI |
| `scripts/evaluate.py` | Referencias verificables, FIFO, prioridad crítica y crecimiento de carga |
| `experiments/golden.json` | Tres consultas fijas con respuestas calculadas independientemente |
| `results/` | Reportes, métricas y artefactos de las ejecuciones verificadas |

## Formato del plan

```json
{
  "name": "Ejemplo mínimo",
  "tasks": [
    {
      "id": "fetch",
      "tool": "demo.echo",
      "arguments": {"value": {"values": [1, 2, 3]}},
      "produces": ["dataset"]
    },
    {
      "id": "stats",
      "tool": "stats.describe",
      "description": "Calcular estadísticas del dataset",
      "arguments": {"values": {"$ref": "fetch.result.values"}},
      "estimated_seconds": 0.2
    }
  ]
}
```

`$ref` conserva objetos, números y listas. `task.result` obtiene el resultado completo; `task.result.rows.0.value` accede a campos e índices. Cada referencia agrega una dependencia automáticamente. Las tareas pueden aparecer en cualquier orden en el JSON.

| Campo de tarea | Uso |
| --- | --- |
| `id`, `tool` | Identificador único y herramienta; obligatorios |
| `arguments` | Objeto JSON que recibe la herramienta |
| `depends_on` | Identificadores que deben terminar antes |
| `consumes`, `produces` | Artefactos lógicos para inferir precedencias |
| `resources` | Mapa de recursos con acceso `read` o `write` |
| `server` | Nombre de servidor MCP definido en `servers` |
| `estimated_seconds` | Peso positivo explícito, en segundos |
| `priority` | Desempate entero después del bottom level |
| `timeout_seconds` | Límite por intento; 30 s por defecto; MCP permite hasta 5 s adicionales de cierre |
| `retries`, `idempotent` | Reintentos adicionales, solo si `idempotent=true` |

`consumes` y `produces` describen precedencia; los datos viajan mediante `arguments` y `$ref`. Un artefacto tiene un único productor. Usa nombres versionados para transformaciones sucesivas. `initial_artifacts` declara artefactos disponibles antes de empezar. Un recurso leído por dos tareas permite paralelismo; si alguna escribe, se introduce precedencia consistente con el DAG existente. Este orden de conflictos es conservador y determinista.

## Análisis JEV y estimación

Hay dos proveedores explícitos:

- **`heuristic`**, predeterminado: línea base sin red. Combina dependencias declaradas, referencias, artefactos, menciones exactas de artefactos en descripciones y conflictos de recursos. No pretende comprender cualquier texto ni ejecuta el modelo Jev.
- **`jev`**: consulta el modelo Jev a través de la [API oficial System One de TypeSafe](https://docs.typesafe.ai/api/system-one). Pregunta por precedencias entre pares y por intervalos de costo. Valida respuestas y rechaza relaciones ambiguas o ciclos. Requiere credenciales propias.

```bash
export TYPESAFE_API_KEY='tu_clave'
# Opcional: export JEV_MODEL='un_modelo_jev_disponible_en_tu_cuenta'
python -m midterm analyze examples/analysis_plan.json --provider jev --no-history --output results/jev_analysis.json
```

`JEV_API_KEY` es un alias aceptado. La clave nunca se escribe en el plan. Se envían a Jev descripciones y metadatos de tareas, sin los valores de `arguments` ni la configuración de servidores. El umbral de la probabilidad de la opción seleccionada para dependencias es 0.75; una estimación probabilística no demuestra independencia. El backend de ejecución valida siempre el DAG resultante. El adaptador se verificó con respuestas controladas; no se hizo una consulta autenticada al servicio, porque no se proporcionó una clave.

El orden de selección del peso es: valor explícito → historial EWMA por herramienta y servidor → intervalo Jev → estimación del retardo de demostración → valor base de 0.1 s. El historial solo aprende de intentos exitosos. Quita `estimated_seconds` de una tarea para observar el aprendizaje. `--no-history` evita leer o escribir SQLite.

## Integración de otras herramientas MCP

Agrega un servidor en `servers` y usa su nombre en las tareas:

```json
{
  "servers": {
    "analytics": {
      "command": "@python",
      "args": ["-m", "midterm.mcp_server"],
      "env": {"SERVICE_TOKEN": "MY_SERVICE_TOKEN"}
    }
  }
}
```

`@python` usa el mismo intérprete que ejecuta el worker. El mapa `env` indica nombres de variables del entorno, no sus valores. El cliente realiza `initialize` y `tools/call`, comprueba `isError`, usa `structuredContent` cuando existe y admite respuestas de texto JSON. Se fija MCP SDK a la línea compatible 1.x. La integración usa la [API oficial del SDK MCP](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x).

El plan es configuración ejecutable de confianza: `servers.command` inicia el programa indicado con argumentos separados, sin shell. Cada tarea abre su propia sesión MCP; declara recursos compartidos para evitar escrituras simultáneas. En varios nodos, el código, intérprete y programas MCP deben existir en cada worker. Las rutas de archivos compartidos y credenciales deben configurarse por nodo; los resultados JSON sí se transmiten por MPI.

## Pruebas y mediciones

```bash
python -m unittest discover -s tests -v
python -m midterm run examples/conflict_plan.json --no-history --output results/conflicts.json
python -m midterm run examples/failure_plan.json --no-history --output results/failures.json
python scripts/benchmark_mpi.py --repeats 3 --workers 1 2 4
python scripts/evaluate.py --repeats 3 --sizes 8 24
python -m midterm run examples/analysis_plan.json --policy fifo --no-history
```

El ejemplo de fallos debe devolver código **1** y marcar el descendiente como `skipped`; sus ramas independientes terminan. Un plan inválido devuelve **2**. Una ejecución correcta devuelve **0**. Si MCP no está instalado, su prueba se marca como omitida; las pruebas del núcleo no requieren paquetes externos.

El benchmark compara los mismos resultados con estimaciones congeladas y sin aprendizaje entre ensayos. `benchmark` mide paralelismo **local**; `scripts/benchmark_mpi.py` utiliza MPI real. Este último escribe cada ensayo, CSV y resumen de medianas. El makespan mide desde el comienzo del scheduler hasta la última respuesta; no incluye análisis Jev ni arranque de `mpiexec`. Incluye el arranque de cada proceso de tarea y su sesión MCP. Los retardos del ejemplo representan latencia; no constituyen una evaluación de rendimiento de producción.

La evaluación de la rúbrica compara baseline secuencial, FIFO y camino crítico bajo el mismo transporte local. Verifica exactitud y completitud contra tres referencias fijas, no solo igualdad con el baseline. Registra hashes entre repeticiones, CPU de procesos de tarea y RSS máximo individual. Este RSS no es la memoria agregada ni incluye servidores hijos. El proveedor heurístico no usa tokens remotos; eso no mide energía ni costo monetario de hardware. Los tiempos MPI previos son exploratorios y se distinguen de la evaluación local controlada.

## Ejecución con contenedor o clúster

```bash
docker build -t midterm-mpi .
docker run --rm midterm-mpi
# En un clúster que ya comparte esta carpeta y el entorno:
mpiexec -n 5 -host nodo1,nodo2 python -m midterm run examples/analysis_plan.json --backend mpi
```

El Dockerfile se proporciona para reproducción; no se verificó Docker en este equipo. El soporte multinodo usa MPI estándar, pero la verificación realizada fue con múltiples procesos en una sola computadora. Ante pérdida de un rank, el watchdog aborta el job: no se implementa recuperación de nodos. Los límites y decisiones del prototipo están descritos en `docs/SOLUCION.md`.
