"""Servidor MCP somente leitura do YouShop Radar (3 Tools, 1 Resource, 1 Prompt)."""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from youshop_radar.config import DEFAULT_MIN_RISK_SCORE, get_session_manager_id
from youshop_radar.data.repository import RadarRepository
from youshop_radar.domain.security import (
    UNAVAILABLE_ERROR_CODE,
    UNAVAILABLE_ERROR_MESSAGE,
    ProducerUnavailableError,
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
        "Servidor MCP somente leitura para detecção preventiva de risco de produtores. "
        "As contas de score são determinísticas; use a IA para correlacionar sinais, explicar em "
        "linguagem simples e propor rascunhos. Nunca invente dados, não exponha classificações "
        "internas ao produtor e nunca sugira contornar bloqueios de carteira."
    ),
)

_repository: RadarRepository | None = None


def get_repository() -> RadarRepository:
    """Inicializa o repositório somente leitura sob demanda (sem efeito colateral no import)."""
    global _repository
    if _repository is None:
        _repository = RadarRepository()
    return _repository


def _unavailable_response(producer_ref: str) -> dict[str, Any]:
    return {
        "error": UNAVAILABLE_ERROR_CODE,
        "message": UNAVAILABLE_ERROR_MESSAGE,
        "producer_ref": producer_ref,
    }


@mcp.tool()
def list_at_risk_producers(min_score: int = DEFAULT_MIN_RISK_SCORE) -> dict[str, Any]:
    """Lista os produtores da carteira autenticada com score de risco >= min_score (0 a 100).

    Use o padrão min_score=50 para focar em produtores que exigem atenção ou alerta técnico
    precoce; use min_score=0 apenas quando o gerente pedir a carteira completa. Somente leitura.
    """
    session_manager_id = get_session_manager_id()
    return list_at_risk_for_session(get_repository(), session_manager_id, min_score=min_score)


@mcp.tool()
def get_producer_signals(producer_ref: str) -> dict[str, Any]:
    """Detalha os sinais de um produtor (por ID ou nome) da carteira autenticada.

    Retorna falhas de webhook (7d), variação de vendas vs. baseline (21d), tickets abertos por
    categoria (qualquer idade) e número de creators ativos. Bloqueia outras carteiras.
    """
    session_manager_id = get_session_manager_id()
    try:
        return get_signals_for_session(get_repository(), session_manager_id, producer_ref)
    except ProducerUnavailableError:
        return _unavailable_response(producer_ref)


@mcp.tool()
def get_signal_history(producer_ref: str, weeks: int = 4) -> dict[str, Any]:
    """Mostra a evolução semanal (1 a 4 semanas) de vendas, falhas de webhook e tickets abertos.

    Útil para descobrir desde quando um sinal começou a piorar ou confirmar estabilidade.
    Bloqueia acesso a produtores de outras carteiras. Somente leitura.
    """
    session_manager_id = get_session_manager_id()
    try:
        return get_history_for_session(
            get_repository(), session_manager_id, producer_ref, weeks=weeks
        )
    except ProducerUnavailableError:
        return _unavailable_response(producer_ref)


@mcp.resource(RETENTION_PLAYBOOK_URI)
def get_retention_playbook() -> str:
    """Guia de exemplo de boas práticas de retenção proativa do Comercial."""
    return RETENTION_PLAYBOOK_TEXT


@mcp.prompt()
def draft_retention_outreach(producer_ref: str, channel: str = "whatsapp") -> str:
    """Gera o prompt estruturado para criar um rascunho de abordagem humana (nunca envia)."""
    return build_outreach_prompt(producer_ref=producer_ref, channel=channel)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()