"""Historial SQLite escrito únicamente por el coordinador."""
import sqlite3
from pathlib import Path


class History:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS samples (tool TEXT, predicted REAL, actual REAL, status TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS estimates (tool TEXT PRIMARY KEY, ewma REAL, count INTEGER)')

    def estimate(self, tool: str) -> float | None:
        row = self.db.execute('SELECT ewma FROM estimates WHERE tool=?', (tool,)).fetchone()
        return row[0] if row else None

    def record(self, tool: str, predicted: float, actual: float, status: str):
        with self.db:
            self.db.execute('INSERT INTO samples VALUES (?, ?, ?, ?)', (tool, predicted, actual, status))
            if status == 'succeeded':
                self.db.execute('INSERT INTO estimates VALUES (?, ?, 1) ON CONFLICT(tool) DO UPDATE SET ewma=0.3*excluded.ewma+0.7*estimates.ewma, count=count+1', (tool, max(actual, 0.000001)))

    def close(self):
        self.db.close()
