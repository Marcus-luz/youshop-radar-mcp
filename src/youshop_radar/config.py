"""Configurações centralizadas do YouShop Radar."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# Semente, data de referência e janelas fixas para resultados reproduzíveis
SEED = 42
REFERENCE_DATE = date(2026, 11, 19)
RECENT_WINDOW_DAYS = 7
BASELINE_WINDOW_DAYS = 21

# Caminho padrão do banco SQLite local
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "youshop_radar.db"

# Pesos do score determinístico (hipóteses iniciais para calibração em piloto)
WEIGHT_WEBHOOK = 0.40
WEIGHT_SALES = 0.40
WEIGHT_TICKETS = 0.20

# Limiares de escala dos subscores (hipóteses iniciais centralizadas)
WEBHOOK_SCALE_CAP_PCT = 30.0
SALES_DROP_SCALE_CAP_PCT = 40.0
CRITICAL_TICKETS_SCALE_CAP = 3.0

# Limiares de classificação e travas de negócio
WEBHOOK_ALERT_THRESHOLD_PCT = 15.0
SALES_DROP_ALERT_THRESHOLD_PCT = -15.0
SALES_STABLE_FLOOR_PCT = -5.0
SEASONAL_WEBHOOK_MAX_PCT = 5.0

FALSE_ALARM_MAX_SCORE = 45
EARLY_TECH_ALERT_MIN_SCORE = 55
EARLY_TECH_IMPACT_BONUS_CAP = 15
SEASONAL_DIP_MAX_SCORE = 35
RISK_HIGH_THRESHOLD = 70
RISK_MEDIUM_THRESHOLD = 50
DEFAULT_MIN_RISK_SCORE = 50

# Classificação de erros de webhook e listas fechadas de valores permitidos
CRITICAL_WEBHOOK_ERRORS = frozenset({"assinatura_invalida", "timeout_checkout_pix"})
AUXILIARY_WEBHOOK_ERRORS = frozenset({"endpoint_analytics_offline"})
ALLOWED_WEBHOOK_ERRORS = (
    CRITICAL_WEBHOOK_ERRORS | AUXILIARY_WEBHOOK_ERRORS | frozenset({"nenhum", "ok"})
)

# Categorias de tickets permitidas e críticas
CRITICAL_TICKET_CATEGORIES = frozenset({"erro_integracao", "falha_checkout"})
ALLOWED_TICKET_CATEGORIES = CRITICAL_TICKET_CATEGORIES | frozenset({"duvida_relatorio"})

# Canais válidos para geração de rascunho no MCP Prompt
ALLOWED_OUTREACH_CHANNELS = frozenset({"whatsapp", "email", "telefone"})


def get_db_path() -> Path:
    """Retorna o caminho do SQLite (aceita override por RADAR_DB_PATH para testes)."""
    override = os.getenv("RADAR_DB_PATH", "").strip()
    return Path(override) if override else DEFAULT_DB_PATH


def get_session_manager_id() -> str:
    """Retorna o ID do gerente autenticado na sessão do servidor MCP (fail-closed).

    Nunca vem de um parâmetro escolhido pelo modelo de IA.
    """
    manager_id = os.getenv("SESSION_MANAGER_ID", "").strip()
    if not manager_id:
        raise RuntimeError(
            "SESSION_MANAGER_ID não configurado na sessão do servidor. "
            "Defina a variável de ambiente antes de iniciar o servidor MCP."
        )
    return manager_id