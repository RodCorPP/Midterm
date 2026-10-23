# Resultados de verificación

La suite final pasó 36 pruebas en 7.762 segundos. El registro está en `results/tests.txt`. La evaluación posterior a la rúbrica incluye 54 ejecuciones locales con tres métodos y tres repeticiones por carga. Las 27 ejecuciones de consultas se comprobaron contra referencias independientes.

| Consulta o carga | Tareas | Secuencial s | FIFO s | Crítica s | Speedup crítica |
| --- | ---: | ---: | ---: | ---: | ---: |
| uniform | 5 | 0.586 | 0.439 | 0.487 | 1.20 |
| constant | 5 | 0.557 | 0.436 | 0.435 | 1.28 |
| outlier | 5 | 0.535 | 0.417 | 0.421 | 1.27 |
| priority_sensitive | 4 | 1.636 | 1.372 | 1.116 | 1.47 |
| scale_8 | 9 | 1.041 | 0.683 | 0.576 | 1.81 |
| scale_24 | 25 | 2.902 | 1.508 | 1.522 | 1.91 |

Los tiempos son medianas de tres repeticiones. Los dos métodos paralelos usan dos workers locales. FIFO empata o mejora a prioridad crítica en algunas cargas; la política crítica no garantiza un makespan menor en todos los casos. No se aisló la computadora de todos los procesos de fondo.

Las consultas fijan estadística poblacional, anomalías e histograma. Exactitud, completitud, corrección de precedencias y consistencia: **1.00**. La tolerancia numérica es 1e-9. Se conservan hashes y observaciones completas en `results/evaluation.json`.

En la carga de 25 tareas, el CPU mediano fue 1.988 s secuencial y 2.017 s con prioridad crítica. La menor latencia no implica ahorro de CPU o energía. El mayor RSS individual fue 25.88 MiB. No incluye memoria agregada de ranks ni servidores MCP hijos.

Se utilizó el proveedor heurístico sin LLM: tokens remotos y costo de API cero. No se midió costo de hardware ni energía. Jev se probó con respuestas controladas y su consulta autenticada sigue pendiente.

La ejecución de siete tareas en MPI con cinco ranks fue correcta, y se conservó el resultado secuencial. El ejemplo MCP real también completó su protocolo tanto localmente como en MPI con cuatro ranks. Los ensayos MPI de escalabilidad previos son exploratorios, con carga de fondo no aislada: sus medianas de dos y cuatro workers no muestran escalamiento monotónico. Consulte `results/scaling.json` y sus reportes crudos.

Reproducción: `python -m unittest discover -s tests -v`, `python scripts/evaluate.py` y `python scripts/benchmark_mpi.py`.
