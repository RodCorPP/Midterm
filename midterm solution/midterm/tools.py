"""Herramientas de ejemplo reproducibles y cliente MCP real mediante stdio."""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import statistics
import sys
import time


def numbers(values):
    if not isinstance(values, list) or not values or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        raise ValueError('values requiere números finitos y al menos un elemento')
    return values


def builtin(name: str, args: dict):
    delay = args.get('delay_seconds', 0)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
        raise ValueError('delay_seconds inválido')
    time.sleep(delay)
    if name == 'demo.echo':
        return args.get('value')
    if name == 'demo.fail':
        raise RuntimeError(args.get('message', 'Fallo de demostración'))
    if name == 'data.generate':
        count = args.get('count', 120)
        if type(count) is not int or not 1 <= count <= 100000:
            raise ValueError('count debe estar entre 1 y 100000')
        rng = random.Random(args.get('seed', 7))
        return {'values': [round(rng.gauss(50, 7), 3) for _ in range(count)] + [120.0, 130.0], 'source': 'synthetic_seeded'}
    if name == 'data.clean':
        values = args['values']
        if not isinstance(values, list):
            raise ValueError('values requiere una lista')
        cleaned = [v for v in values if type(v) in (int, float) and math.isfinite(v)]
        numbers(cleaned)
        return {'values': cleaned, 'removed': len(values) - len(cleaned)}
    if name == 'stats.describe':
        values = numbers(args['values'])
        return {'count': len(values), 'mean': statistics.mean(values), 'median': statistics.median(values), 'stddev': statistics.pstdev(values), 'min': min(values), 'max': max(values)}
    if name == 'stats.anomalies':
        values = numbers(args['values'])
        mean, sd = statistics.mean(values), statistics.pstdev(values)
        threshold = args.get('z_threshold', 3.0)
        if not isinstance(threshold, (float, int)) or isinstance(threshold, bool) or not math.isfinite(threshold) or threshold <= 0:
            raise ValueError('z_threshold inválido')
        outliers = [{'index': i, 'value': v, 'z': (v - mean) / sd} for i, v in enumerate(values) if sd and abs((v - mean) / sd) > threshold]
        return {'method': 'population_z_score', 'threshold': threshold, 'outliers': outliers}
    if name == 'chart.histogram':
        values = numbers(args['values'])
        bins = args.get('bins', 10)
        if type(bins) is not int or not 1 <= bins <= 100:
            raise ValueError('bins debe estar entre 1 y 100')
        lo, hi = min(values), max(values)
        width = (hi - lo) / bins or 1
        counts = [0] * bins
        for v in values:
            counts[min(bins - 1, int((v - lo) / width))] += 1
        bars = ''.join(f'<rect x="{45 + i * 46}" y="{260 - c / max(counts) * 210:.1f}" width="38" height="{c / max(counts) * 210:.1f}" fill="#2563eb"/>' for i, c in enumerate(counts))
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {max(560, 60 + bins * 46)} 310"><rect width="100%" height="100%" fill="white"/><text x="30" y="25" font-family="sans-serif" font-size="18">Distribución de valores</text>{bars}<text x="30" y="295" font-family="sans-serif">Mínimo {lo:.1f} · Máximo {hi:.1f} · n={len(values)}</text></svg>'
        return {'counts': counts, 'minimum': lo, 'maximum': hi, 'svg': svg}
    if name == 'report.compose':
        stats, anomalies = args['statistics'], args['anomalies']
        return {'markdown': f"# Análisis de datos\n\nSe analizaron {stats['count']} observaciones. La media es {stats['mean']:.3f} y la desviación estándar poblacional es {stats['stddev']:.3f}.\n\nSe detectaron {len(anomalies['outliers'])} anomalías con el método de puntuación z.\n\nEl conjunto es sintético y reproducible; los resultados describen exclusivamente este ejemplo.\n", 'statistics': stats, 'anomalies': anomalies}
    raise ValueError(f'Herramienta builtin desconocida: {name}')


async def call_mcp(config: dict, name: str, arguments: dict):
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise RuntimeError('Instala la dependencia opcional mcp>=1.28,<2') from exc
    env = os.environ.copy()
    for target, source in config.get('env', {}).items():
        if source not in os.environ:
            raise ValueError(f'Falta variable de entorno {source}')
        env[target] = os.environ[source]
    command = sys.executable if config['command'] == '@python' else config['command']
    params = StdioServerParameters(command=command, args=config.get('args', []), env=env, cwd=config.get('cwd'))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            if result.isError:
                raise RuntimeError('La herramienta MCP devolvió isError=true')
            if result.structuredContent is not None:
                return result.structuredContent
            texts = [c.text for c in result.content if c.type == 'text']
            if len(texts) == 1:
                try:
                    return json.loads(texts[0])
                except json.JSONDecodeError:
                    return {'text': texts[0]}
            return {'content': [c.model_dump(mode='json', by_alias=True) for c in result.content]}


def invoke(tool: str, arguments: dict, server: dict | None = None, timeout_seconds: float = 30):
    if not server:
        return builtin(tool, arguments)

    async def bounded_call():
        # Cancelar dentro del contexto permite al SDK cerrar su propio grupo
        # de procesos, que es distinto del grupo del worker.
        async with asyncio.timeout(timeout_seconds):
            return await call_mcp(server, tool, arguments)

    return asyncio.run(bounded_call())
