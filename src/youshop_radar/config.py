"""Configurações centralizadas do YouShop Radar."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# Semente e data de referência fixas para resultados 100% reproduzíveis
SEED = 42
REFERENCE_DATE = date(2026, 11, 19)

# Caminho padrão do banco SQLite local
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "youshop_radar.db"

# Pesos do score determinístico (hipóteses iniciais para calibração em piloto)
WEIGHT_WEBHOOK = 0.40
WEIGHT_SALES = 0.40
WEIGHT_TICKETS = 0.20

# Categorias de tickets que indicam fricção operacional crítica
CRITICAL_TICKET_CATEGORIES = ("erro_integracao", "falha_checkout")


def get_session_manager_id() -> str:
    """Retorna o ID do gerente autenticado na sessão do servidor MCP.

    Nunca vem de um parâmetro escolhido pelo modelo de IA.
    """
    return os.getenv("SESSION_MANAGER_ID", "mgr_ana").strip()