"""Servidor MCP somente leitura do YouShop Radar (3 Tools, 1 Resource, 1 Prompt)."""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from youshop_radar.config import get_session_manager_id
from youshop_radar.data.repository import RadarRepository
from youshop_radar.domain.security import (
    PortfolioAccessError,
    get_history_for_session,
    get_signals_for_session,
    list_at_risk_for_session,
)
from youshop_radar.mcp_server.playbooks import (
    RETENTION_PLAYBOOK_TEXT,
    RETENTION_PLAYBOOK_URI,
    build_outreach_prompt,
)

load_dotenv()

mcp = MCPServer(
    "YouShop Radar",
    instructions=(
        "Servidor MCP somente leitura para detecção preventiva de risco de produtores na YouShop. "
        "As contas de score são determinísticas; use a IA para correlacionar sinais, explicar em "
        "linguagem simples e propor rascunhos. Nunca invente dados quando houver recusa de carteira "
        "ou falso alarme técnico."
    ),
)

_repository = RadarRepository()


@mcp.tool()
def list_at_risk_producers(min_score: int = 50) -> dict[str, Any]:
    """Lista os produtores da carteira autenticada com score de risco >= min_score (0 a 100).

    Use min_score=50 para focar em risco médio/alto ou min_score=0 para ver todos os produtores
    da carteira (incluindo falso alarme técnico e sazonalidade). Somente leitura.
    """
    session_manager_id = get_session_manager_id()
    return list_at_risk_for_session(_repository, session_manager_id, min_score=min_score)


@mcp.tool()
def get_producer_signals(producer_ref: str) -> dict[str, Any]:
    """Detalha os sinais dos últimos 7 dias de um produtor (por ID ou nome) da carteira atual.

    Retorna falhas de webhook, variação de vendas vs. média de 21 dias, tickets estruturados por
    categoria e número de creators ativos. Bloqueia acesso a produtores de outras carteiras.
    """
    session_manager_id = get_session_manager_id()
    try:
        return get_signals_for_session(_repository, session_manager_id, producer_ref)
    except PortfolioAccessError as exc:
        return {
            "error": "ACCESS_DENIED_PORTFOLIO",
            "message": str(exc),
            "producer_ref": producer_ref,
        }
    except LookupError as exc:
        return {
            "error": "PRODUCER_NOT_FOUND",
            "message": str(exc),
            "producer_ref": producer_ref,
        }


@mcp.tool()
def get_signal_history(producer_ref: str, weeks: int = 4) -> dict[str, Any]:
    """Mostra a evolução semanal (1 a 4 semanas) de vendas, falhas de webhook e tickets.

    Útil para descobrir desde quando um sinal começou a piorar ou confirmar sazonalidade.
    Bloqueia acesso a produtores de outras carteiras. Somente leitura.
    """
    session_manager_id = get_session_manager_id()
    try:
        return get_history_for_session(_repository, session_manager_id, producer_ref, weeks=weeks)
    except PortfolioAccessError as exc:
        return {
            "error": "ACCESS_DENIED_PORTFOLIO",
            "message": str(exc),
            "producer_ref": producer_ref,
        }
    except LookupError as exc:
        return {
            "error": "PRODUCER_NOT_FOUND",
            "message": str(exc),
            "producer_ref": producer_ref,
        }


@mcp.resource(RETENTION_PLAYBOOK_URI)
def get_retention_playbook() -> str:
    """Guia oficial de boas práticas de retenção proativa do Comercial da YouShop."""
    return RETENTION_PLAYBOOK_TEXT


@mcp.prompt()
def draft_retention_outreach(producer_ref: str, channel: str = "whatsapp") -> str:
    """Gera o prompt estruturado para criar um rascunho de abordagem humana (nunca envia)."""
    return build_outreach_prompt(producer_ref=producer_ref, channel=channel)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()