# Solución del planificador distribuido

## Objetivo y alcance

El sistema implementa la arquitectura planteada: plan del agente en JSON, caracterización de tareas, DAG ponderado, scheduler topológico, coordinador MPI, workers que ejecutan herramientas y retroalimentación de costos reales. Su función es evitar secuencias innecesarias y comenzar pronto las ramas que más condicionan el tiempo total.

El agente LLM es un productor externo del contrato JSON. No hace falta una cuenta de un proveedor LLM para demostrar el proyecto: los planes de ejemplo representan su salida. El documento no determina lenguaje, versión de MPI, API JEV ni contratos de herramientas. La solución utiliza Python 3.11+, mpi4py 4.x y MCP SDK 1.x. Se implementa una interfaz al modelo Jev de TypeSafe y una línea base determinista independiente para reproducir la evaluación.

## Correspondencia con la propuesta

| Requisito de la propuesta | Implementación | Evidencia |
| --- | --- | --- |
| Recibir tareas MCP en JSON | `Plan.load`, validación de campos y referencias | Ejemplos y pruebas del contrato |
| Inferir dependencias implícitas | Referencias, artefactos, descripciones y proveedor Jev | Pruebas de análisis y auditoría de aristas |
| Detectar ambigüedad y conflictos | Rechazo por probabilidad baja; conflictos read/write | Pruebas Jev controladas y plan de conflictos |
| Estimar costo por tarea | Peso explícito, historial, Jev y heurística | Fuentes de peso registradas en el reporte |
| Construir DAG ponderado | Adyacencias, Kahn, pesos y exportación DOT | `results/analysis.json` y `.dot` |
| Mantener tareas con indegree cero | Heap de tareas listas y decremento tras éxito | Pruebas de precedencia y cronogramas |
| Priorizar ramas críticas | Bottom level descendente | Prueba de selección de rama pesada |
| Coordinar desde rank 0 | `MPITransport` y scheduler único | Ejecuciones MPI verificadas |
| Ejecutar MCP en workers | Cliente stdio y servidor de análisis | Prueba de protocolo MCP real |
| Mejorar pesos con duración real | SQLite con EWMA y muestras | Pruebas del historial |
| Comparar makespan y resultados | Baseline secuencial y benchmarks | Reportes JSON, HTML y CSV |

## Flujo de ejecución

```text
Agente o plan de ejemplo
         |
         v
Validación del JSON --> Analizador heurístico o Jev
                               |
                               v
                    Dependencias y pesos
                               |
                               v
                    DAG válido y bottom levels
                               |
                               v
                    Rank 0 y heap de tareas listas
                        /      |       \
                   Rank 1   Rank 2   Rank N
                    tarea    tarea    tarea
                        \      |       /
                         resultados
                               |
                               v
              Actualización de indegrees e historial
                               |
                               v
                   JSON, HTML y artefactos
```

El rank 0 mantiene todo el estado del DAG. Cada mensaje de trabajo contiene una tarea, sus argumentos ya resueltos y, cuando corresponde, la configuración MCP. Los workers no necesitan conocer el grafo completo. Cada respuesta lleva estado, resultado o error y duración. El coordinador despacha un nuevo trabajo al primer worker que queda libre; no espera una barrera entre niveles del DAG.

## Inferencia y validación

Las aristas tienen una o varias razones auditables. Se conservan todas las dependencias declaradas y derivadas de `$ref`. Un artefacto consumido exige un productor único o una declaración en `initial_artifacts`. La regla de descripción detecta el nombre exacto de un artefacto producido por otra tarea, ignorando mayúsculas y acentos. Este procedimiento es deliberadamente acotado: no garantiza resolver lenguaje natural arbitrario.

El proveedor Jev formula una pregunta `choice` por pareja: A precede a B, B precede a A o son independientes. Otras preguntas clasifican el costo en 0.05, 0.2, 1, 5 o 30 segundos. Se procesan lotes de 24 preguntas sobre los mismos metadatos. Se registran decisiones y probabilidades. Una relación con probabilidad seleccionada menor que 0.75 bloquea el plan; una respuesta que introduce ciclos también lo bloquea. La probabilidad del modelo es una señal y no una garantía de corrección. La API permite configurar el modelo con `JEV_MODEL`.

Los accesos a recursos se ordenan después de validar las dependencias. Entre dos lectores no se agrega arista. Si al menos una tarea escribe, se agrega precedencia según un orden topológico estable. Esto evita inventar ciclos cuando el JSON enumera tareas en orden inverso a sus dependencias y preserva el orden de listado para tareas incomparables. Los artefactos describen productos inmutables; los recursos describen estado compartido mutable.

## Algoritmo de planificación

Para cada tarea v se calcula:

```text
bottom_level(v) = weight(v) + max(bottom_level(hijo))
max del conjunto vacío = 0
```

El cálculo recorre el orden topológico de atrás hacia adelante. Una hoja tiene como prioridad el peso de su propia ejecución. Un nodo con sucesores acumula la rama restante más costosa.

```text
validar DAG y calcular bottom levels
indegree[v] = cantidad de padres únicos
insertar todos los nodos con indegree cero en el heap
mientras existan tareas listas o en ejecución:
    mientras haya un worker libre y una tarea lista:
        elegir mayor bottom level
        desempatar por prioridad explícita y orden del plan
        resolver referencias a resultados terminados
        enviar la tarea al worker
    recibir la siguiente respuesta disponible
    si terminó correctamente:
        guardar resultado
        decrementar indegree de sus hijos
        insertar los nuevos hijos listos
    si falló y quedan reintentos:
        devolver la tarea al heap
    si agotó los intentos:
        marcar fallo y omitir descendientes
```

Los pesos se fijan al comenzar cada ejecución. Las mediciones actualizan el historial para ejecuciones futuras, evitando alterar prioridades a mitad del benchmark. El algoritmo es una heurística de list scheduling: priorizar la rama crítica busca reducir makespan, pero no garantiza el óptimo global.

Kahn y las prioridades usan estructuras de adyacencia y heaps. El recorrido del grafo y el despacho tienen costo aproximado O((V + E) log V). La comparación de parejas para recursos y Jev es O(V²), apropiada para los planes pequeños del prototipo. La propagación de fallos recorre descendientes pendientes y puede repetir visitas entre fallos distintos.

## Contrato de MPI y ejecución MCP

Los tags son `TASK=10`, `RESULT=11` y `STOP=12`. Rank 0 recibe respuestas con `ANY_SOURCE`, observa el emisor y reutiliza ese worker. Los ranks restantes esperan trabajo, ejecutan una tarea y reportan su duración. Al agotarse el plan se envía STOP a todos, incluidos workers que nunca recibieron una tarea. Si el plan es inválido, se comunica el error inicial por broadcast antes de entrar al bucle de workers.

Cada tarea se ejecuta en un subproceso del rank o worker local. Esto permite cortar un cómputo que exceda el timeout. Las llamadas MCP tienen además un timeout asíncrono: su cancelación deja al SDK cerrar el servidor, que crea un grupo de procesos propio. Se permiten hasta 5 segundos adicionales para ese cierre antes de terminar el worker. En POSIX se termina el grupo del worker si se agota el límite externo; un servidor que ignore su propia limpieza requiere supervisión adicional. Los reintentos son secuenciales por tarea y solo se permiten con `idempotent=true`; la persona que define la herramienta debe garantizar esa propiedad. En Windows la terminación del árbol completo necesita un mecanismo adicional de job objects.

El cliente MCP inicia el servidor configurado, negocia `initialize`, llama la herramienta y cierra la sesión. El prototipo usa stdio. No se implementó transporte HTTP ni persistencia de sesiones MCP. Los costos observados incluyen su negociación. Los mensajes MPI transmiten objetos de Python serializables, por lo que todos los ranks deben ejecutar el mismo código y confiar entre sí.

## Aprendizaje y evaluación

El historial conserva muestras con peso predicho, duración real y estado. Para herramientas con ejecuciones exitosas, mantiene:

```text
EWMA_nueva = 0.3 * duracion_observada + 0.7 * EWMA_anterior
```

La primera observación inicializa el estimador. Solo rank 0 escribe el historial en MPI. La clave combina servidor y nombre de herramienta; no distingue tamaños de entrada ni tipos de nodo. El peso explícito conserva precedencia, por lo que debe omitirse cuando se quiera usar aprendizaje.

Las métricas son makespan, segundos ocupados por workers, utilización aproximada, speedup y eficiencia. La utilización divide la suma de duraciones medidas en workers entre el makespan por cantidad de workers. El speedup divide tiempo secuencial entre tiempo paralelo. La eficiencia divide speedup entre workers; el coordinador no se cuenta como worker.

El ejemplo genera 122 observaciones reproducibles, limpia los valores y abre tres ramas para estadísticas, anomalías y un histograma. Una tarea de contexto independiente comienza en paralelo y el resumen espera todos sus insumos. Los retardos explícitos simulan herramientas con latencia. La igualdad de resultados se verifica sobre el JSON completo de tareas, excluyendo tiempos.

## Límites y extensiones

La solución está implementada y verificada para planes de demostración y MCP stdio. La consulta autenticada a Jev necesita una clave de TypeSafe; su contrato HTTP y manejo de respuestas se prueban sin credenciales mediante respuestas controladas. Las pruebas MPI usan varios procesos en un mismo equipo, no varias máquinas. El contenedor se incluye sin afirmar que fue ejecutado.

No existe recuperación transparente tras perder un rank: el watchdog aborta el job después del timeout de tarea más 15 segundos. No se persiste el estado del DAG para reanudar un job interrumpido. Los resultados completos viajan por el coordinador y permanecen en memoria; conjuntos grandes deberían usar almacenamiento compartido y referencias. Los modelos de costo podrían mejorar con tamaño de entrada, clase de nodo y percentiles de duración.

El scheduler valida el orden que conoce; la calidad del DAG depende de declarar los insumos y recursos relevantes. Para operaciones externas debe revisarse el plan y registrarse todo recurso compartido. El archivo JSON puede iniciar programas MCP configurados; no es un formato para aceptar código arbitrario de fuentes desconocidas.

## Referencias técnicas

- [mpi4py Tutorial](https://mpi4py.github.io/mpi4py/stable/html/tutorial.html), comunicación de objetos y primitivas MPI.
- [mpi4py Installation](https://mpi4py.github.io/mpi4py/stable/html/install.html), runtime MPI y paquetes compatibles.
- [MCP Python SDK 1.x](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x), cliente stdio y FastMCP.
- [TypeSafe System One API](https://docs.typesafe.ai/api/system-one), contrato de solicitudes y respuestas Jev.
- [TypeSafe Choice](https://docs.typesafe.ai/primitives/choice), opciones y distribución de probabilidades.
- [TypeSafe Request Rules](https://docs.typesafe.ai/api/errors), errores HTTP y límites del cuerpo.
