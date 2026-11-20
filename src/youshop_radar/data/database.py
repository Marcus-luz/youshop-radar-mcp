"""Schema SQLite e gerenciamento de conexões."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from youshop_radar.config import DEFAULT_DB_PATH, get_db_path

SCHEMA_SQL = """
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS webhook_events;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS producers;
DROP TABLE IF EXISTS managers;

CREATE TABLE managers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL
);

CREATE TABLE producers (
    id TEXT PRIMARY KEY,
    manager_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    active_creators_count INTEGER NOT NULL,
    seasonal_pattern INTEGER NOT NULL DEFAULT 0,
    -- Dados sensíveis simulados (NUNCA expostos ao agente MCP)
    owner_email TEXT NOT NULL,
    tax_id TEXT NOT NULL,
    webhook_secret TEXT NOT NULL,
    FOREIGN KEY (manager_id) REFERENCES managers(id)
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    amount_brl REAL NOT NULL,
    status TEXT NOT NULL,
    -- Dado pessoal simulado (NUNCA exposto ao agente MCP)
    buyer_email TEXT NOT NULL,
    FOREIGN KEY (producer_id) REFERENCES producers(id)
);

CREATE TABLE webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    error_category TEXT NOT NULL,
    FOREIGN KEY (producer_id) REFERENCES producers(id)
);

CREATE TABLE tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producer_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    -- Apenas categoria estruturada (validada contra lista fixa antes de sair do servidor)
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY (producer_id) REFERENCES producers(id)
);
"""


def open_write_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Abre conexão de escrita (usada apenas pelo gerador de dados simulados)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def open_readonly_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Abre conexão estritamente somente leitura para uso do servidor MCP."""
    path = Path(db_path).resolve() if db_path is not None else get_db_path().resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"Banco SQLite não encontrado em '{path}'. "
            "Execute 'uv run python -m youshop_radar.data.seed' para gerar a base simulada."
        )
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn