"""Servidor MCP de demostración. stdout queda reservado para JSON-RPC."""
from mcp.server.fastmcp import FastMCP

from .tools import builtin

server = FastMCP('midterm-analytics')


@server.tool()
def generate(count: int = 120, seed: int = 7) -> dict:
    """Genera observaciones reproducibles y dos valores extremos."""
    return builtin('data.generate', {'count': count, 'seed': seed})


@server.tool()
def describe(values: list[float], delay_seconds: float = 0) -> dict:
    """Calcula estadísticas descriptivas poblacionales."""
    return builtin('stats.describe', {'values': values, 'delay_seconds': delay_seconds})


@server.tool()
def anomalies(values: list[float], z_threshold: float = 3, delay_seconds: float = 0) -> dict:
    """Detecta anomalías mediante una puntuación z poblacional."""
    return builtin('stats.anomalies', {'values': values, 'z_threshold': z_threshold, 'delay_seconds': delay_seconds})


@server.tool()
def compose(statistics: dict, anomalies: dict) -> dict:
    """Combina resultados ya calculados en un resumen en español."""
    return builtin('report.compose', {'statistics': statistics, 'anomalies': anomalies})


if __name__ == '__main__':
    server.run(transport='stdio')
