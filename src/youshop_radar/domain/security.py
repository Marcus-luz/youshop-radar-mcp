"""Camada de segurança: isolamento por carteira (sessão) e Allowlist LGPD."""

from __future__ import annotations

from typing import Any

from youshop_radar.data.repository import RadarRepository
from youshop_radar.domain.scoring import compute_producer_signals, compute_weekly_history

# Lista explícita de campos permitidos nas respostas (Allowlist — princípio da necessidade)
ALLOWED_SUMMARY_FIELDS = frozenset(
    {
        "producer_id",
        "producer_name",
        "category",
        "active_creators_count",
        "risk_score",
        "risk_level",
        "main_signal",
    }
)

ALLOWED_DETAIL_FIELDS = frozenset(
    {
        "producer_id",
        "producer_name",
        "category",
        "active_creators_count",
        "risk_score",
        "risk_level",
        "signals_last_7d",
        "calibration_note",
    }
)

ALLOWED_SIGNAL_FIELDS = frozenset(
    {
        "webhook_failure_rate_pct",
        "webhook_failed_events",
        "webhook_total_events",
        "top_webhook_error",
        "sales_variation_vs_baseline_pct",
        "recent_orders_count",
        "open_tickets_total",
        "open_critical_tickets",
        "tickets_by_category",
        "known_seasonal_pattern",
    }
)

ALLOWED_HISTORY_FIELDS = frozenset(
    {
        "week_range",
        "orders_count",
        "revenue_brl",
        "webhook_failure_rate_pct",
        "tickets_opened",
    }
)


class PortfolioAccessError(PermissionError):
    """Lançada quando o gerente autenticado tenta acessar produtor de outra carteira."""


def _filter_allowlist(data: dict[str, Any], allowed_keys: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key in allowed_keys}


def _describe_main_signal(signals: dict[str, Any]) -> str:
    s7 = signals["signals_last_7d"]
    parts: list[str] = []
    if s7["webhook_failure_rate_pct"] >= 15.0:
        parts.append(
            f"webhooks falhando ({s7['webhook_failure_rate_pct']}% - {s7['top_webhook_error']})"
        )
    if s7["sales_variation_vs_baseline_pct"] <= -15.0:
        parts.append(f"vendas {s7['sales_variation_vs_baseline_pct']}% vs. média")
    if s7["open_critical_tickets"] > 0:
        parts.append(f"{s7['open_critical_tickets']} ticket(s) crítico(s) aberto(s)")
    return "; ".join(parts) if parts else "operação estável nos últimos 7 dias"


def _require_owned_producer(
    repo: RadarRepository, session_manager_id: str, producer_ref: str
) -> dict[str, Any]:
    producer = repo.get_producer(producer_ref)
    if producer is None:
        raise LookupError(f"Produtor não encontrado: {producer_ref}")
    if producer["manager_id"] != session_manager_id:
        raise PortfolioAccessError(
            "ACCESS_DENIED_PORTFOLIO: Acesso negado. O produtor solicitado não pertence "
            "à carteira autenticada na sessão atual."
        )
    return producer


def list_at_risk_for_session(
    repo: RadarRepository,
    session_manager_id: str,
    min_score: int = 50,
) -> dict[str, Any]:
    """Lista apenas os produtores da carteira autenticada com score >= min_score."""
    min_score = max(0, min(100, int(min_score)))
    producer_ids = repo.list_producer_ids_by_manager(session_manager_id)

    items: list[dict[str, Any]] = []
    for pid in producer_ids:
        raw = compute_producer_signals(repo, pid)
        if raw["risk_score"] >= min_score:
            summary_candidate = {
                **raw,
                "main_signal": _describe_main_signal(raw),
            }
            items.append(_filter_allowlist(summary_candidate, ALLOWED_SUMMARY_FIELDS))

    items.sort(key=lambda item: item["risk_score"], reverse=True)
    return {
        "session_manager_id": session_manager_id,
        "min_score_filter": min_score,
        "total_portfolio_producers": len(producer_ids),
        "flagged_count": len(items),
        "producers": items,
        "data_notice": "Dados simulados (seed=42). Retorno filtrado por Allowlist LGPD.",
    }


def get_signals_for_session(
    repo: RadarRepository,
    session_manager_id: str,
    producer_ref: str,
) -> dict[str, Any]:
    """Retorna os sinais detalhados de um produtor, validando posse e Allowlist."""
    producer = _require_owned_producer(repo, session_manager_id, producer_ref)
    raw = compute_producer_signals(repo, producer["id"])

    safe_signals = _filter_allowlist(raw["signals_last_7d"], ALLOWED_SIGNAL_FIELDS)
    safe_detail = _filter_allowlist(raw, ALLOWED_DETAIL_FIELDS)
    safe_detail["signals_last_7d"] = safe_signals
    return safe_detail


def get_history_for_session(
    repo: RadarRepository,
    session_manager_id: str,
    producer_ref: str,
    weeks: int = 4,
) -> dict[str, Any]:
    """Retorna a evolução semanal de um produtor da carteira autenticada."""
    producer = _require_owned_producer(repo, session_manager_id, producer_ref)
    raw_weeks = compute_weekly_history(repo, producer["id"], weeks=weeks)
    safe_weeks = [_filter_allowlist(item, ALLOWED_HISTORY_FIELDS) for item in raw_weeks]

    return {
        "producer_id": producer["id"],
        "producer_name": producer["name"],
        "weeks_analyzed": len(safe_weeks),
        "weekly_evolution": safe_weeks,
    }