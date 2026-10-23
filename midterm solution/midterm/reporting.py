"""Reportes portables: JSON y HTML autocontenido con cronograma observado."""
from __future__ import annotations

import html
import json
from pathlib import Path


def write_json(path: str | Path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def save_report(path: str | Path, report: dict):
    path = Path(path)
    write_json(path, report)
    esc = html.escape
    colors = ['#2563eb', '#0d9488', '#9333ea', '#ea580c', '#db2777']
    rows = ''
    makespan = report['makespan_seconds'] or 1
    for e in sorted(report['events'], key=lambda e: e['start_seconds']):
        left = e['start_seconds'] / makespan * 100
        width = max(0.2, (e['end_seconds'] - e['start_seconds']) / makespan * 100)
        color = colors[(e['worker'] - 1) % len(colors)] if e['status'] == 'succeeded' else '#dc2626'
        rows += f'<tr><td>{esc(e["task"])}<small>Worker {e["worker"]} · intento {e["attempt"]}</small></td><td class="track"><span style="left:{left:.3f}%;width:{width:.3f}%;background:{color}" title="{esc(e["task"])}"></span></td><td>{e["duration_seconds"]:.3f} s</td></tr>'
    edges = ''.join(f'<li><code>{esc(e["from"])}</code> → <code>{esc(e["to"])}</code> <small>{esc(", ".join(e["reasons"]))}</small></li>' for e in report['dag']['edges'])
    statuses = ''.join(f'<tr><td>{esc(t)}</td><td>{esc(s)}</td><td>{esc(report["errors"].get(t, ""))}</td></tr>' for t, s in report['states'].items())
    details = esc(json.dumps(report['results'], indent=2, ensure_ascii=False))
    document = f'''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Reporte del planificador</title>
<style>body{{font:16px system-ui,sans-serif;background:#f5f7fb;color:#172033;max-width:1100px;margin:40px auto;padding:0 24px}}h1{{font-size:32px}}h2{{margin-top:36px}}.meta{{color:#526078}}.metrics{{display:flex;gap:16px;flex-wrap:wrap}}.metric{{background:white;padding:20px;min-width:150px;border-radius:12px;border:1px solid #dbe1eb}}.metric strong{{display:block;font-size:28px;margin-top:8px}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:12px;border-bottom:1px solid #dde3eb;text-align:left}}small{{display:block;color:#526078}}.track{{position:relative;width:65%;background:repeating-linear-gradient(90deg,#fff,#fff 9.9%,#e8edf3 10%)}}.track span{{position:absolute;top:18px;height:22px;border-radius:4px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:20px;border-radius:12px}}code{{font:14px ui-monospace,monospace}}li{{margin-bottom:8px}}</style>
<body><p class="meta">MIDTERM · PLANIFICACIÓN DISTRIBUIDA</p><h1>{esc(report['plan'])}</h1><p>Backend: <b>{esc(report['backend'])}</b> · Analizador: <b>{esc(report['analysis']['provider'])}</b> · Resultado: <b>{'Correcto' if report['ok'] else 'Con errores'}</b></p>
<div class="metrics"><div class="metric">Tiempo total<strong>{report['makespan_seconds']:.3f} s</strong></div><div class="metric">Workers<strong>{report['workers']}</strong></div><div class="metric">Tareas correctas<strong>{sum(s == 'succeeded' for s in report['states'].values())}/{len(report['states'])}</strong></div><div class="metric">Utilización estimada<strong>{report['utilization']:.1%}</strong></div></div>
<h2>Cronograma observado</h2><p class="meta">Cada barra usa tiempos del coordinador. La duración incluye el proceso aislado y, para MCP, la conexión al servidor.</p><table><thead><tr><th>Tarea</th><th>0 a {makespan:.3f} segundos</th><th>Duración</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Dependencias y sus razones</h2><ul>{edges}</ul><h2>Estado final</h2><table><tr><th>Tarea</th><th>Estado</th><th>Error</th></tr>{statuses}</table><h2>Resultados</h2><details><summary>Ver resultados completos</summary><pre>{details}</pre></details></body></html>'''
    path.with_suffix('.html').write_text(document, encoding='utf-8')
    # Materializar artefactos conocidos de nuestras herramientas, con ids validados.
    artifacts = path.parent / f'{path.stem}_artifacts'
    for task, result in report['results'].items():
        if isinstance(result, dict):
            for key, extension in [('svg', '.svg'), ('markdown', '.md')]:
                if isinstance(result.get(key), str):
                    artifacts.mkdir(exist_ok=True)
                    (artifacts / f'{task}{extension}').write_text(result[key], encoding='utf-8')
