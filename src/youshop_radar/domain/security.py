"""Camada de segurança: isolamento por carteira (sessão) e minimização via Allowlist."""

from __future__ import annotations

import re
from typing import Any

from youshop_radar.config import (
    ALLOWED_TICKET_CATEGORIES,
    ALLOWED_WEBHOOK_ERRORS,
    BASELINE_WINDOW_DAYS,
    DEFAULT_MIN_RISK_SCORE,
    RECENT_WINDOW_DAYS,
    SALES_DROP_ALERT_THRESHOLD_PCT,
    WEBHOOK_ALERT_THRESHOLD_PCT,
)
from youshop_radar.data.repository import RadarRepository
from youshop_radar.domain.scoring import compute_producer_signals, compute_weekly_history

# Lista explícita de campos permitidos nas respostas (minimização de dados)
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
        "recent_window_days",
        "baseline_window_days",
        "webhook_failure_rate_pct",
        "webhook_failed_events",
        "webhook_total_events",
        "top_webhook_error",
        "webhook_errors_by_category",
        "sales_variation_vs_baseline_pct",
        "baseline_status",
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

UNAVAILABLE_ERROR_CODE = "PRODUCER_UNAVAILABLE"
UNAVAILABLE_ERROR_MESSAGE = "Produtor não encontrado na carteira autenticada."

_PRODUCER_REF_RE = re.compile(r"^[A-Za-z0-9À-ÿ _.\-&/]{1,80}\Z")


class ProducerUnavailableError(PermissionError):
    """Lançada quando o produtor não existe ou não pertence à carteira da sessão.

    Usa resposta única para evitar enumeração de clientes de outras carteiras.
    """

def sanitize_producer_ref(producer_ref: str) -> str:
    """Valida formato de `producer_ref`, bloqueando quebras de linha e injeção."""
    if not isinstance(producer_ref, str):
        raise TypeError("Referência de produtor deve ser texto.")
    if "\n" in producer_ref or "\r" in producer_ref or "\t" in producer_ref:
        raise ValueError("Referência de produtor não pode conter quebras de linha ou tabulação.")
    cleaned = producer_ref.strip()
    if not cleaned or not _PRODUCER_REF_RE.fullmatch(cleaned):
        raise ValueError(
            "Referência de produtor inválida: use apenas ID ou nome (até 80 caracteres, "
            "sem pontuação especial ou comandos)."
        )
    return cleaned

def _filter_allowlist(data: dict[str, Any], allowed_keys: frozenset[str]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key in allowed_keys}


def _sanitize_webhook_error(value: Any) -> str:
    if isinstance(value, str) and value in ALLOWED_WEBHOOK_ERRORS:
        return value
    return "erro_nao_catalogado"


def _sanitize_webhook_errors_map(raw_errors: dict[str, int]) -> dict[str, int]:
    sanitized: dict[str, int] = {}
    for err, qty in raw_errors.items():
        safe_key = _sanitize_webhook_error(err)
        sanitized[safe_key] = sanitized.get(safe_key, 0) + int(qty)
    return sanitized


def _sanitize_tickets_by_category(raw_tickets: dict[str, int]) -> dict[str, int]:
    sanitized: dict[str, int] = {}
    for category, qty in raw_tickets.items():
        safe_key = category if category in ALLOWED_TICKET_CATEGORIES else "outros"
        sanitized[safe_key] = sanitized.get(safe_key, 0) + int(qty)
    return sanitized


def _sanitize_signals_block(raw_signals: dict[str, Any]) -> dict[str, Any]:
    filtered = _filter_allowlist(raw_signals, ALLOWED_SIGNAL_FIELDS)
    filtered["top_webhook_error"] = _sanitize_webhook_error(filtered.get("top_webhook_error"))
    raw_wh_errors = filtered.get("webhook_errors_by_category") or {}
    filtered["webhook_errors_by_category"] = _sanitize_webhook_errors_map(raw_wh_errors)
    raw_tickets = filtered.get("tickets_by_category") or {}
    filtered["tickets_by_category"] = _sanitize_tickets_by_category(raw_tickets)
    return filtered


def _describe_main_signal(signals: dict[str, Any], safe_s7: dict[str, Any]) -> str:
    parts: list[str] = []
    if safe_s7["webhook_failure_rate_pct"] >= WEBHOOK_ALERT_THRESHOLD_PCT:
        parts.append(
            f"webhooks falhando ({safe_s7['webhook_failure_rate_pct']}% - "
            f"{safe_s7['top_webhook_error']})"
        )
    sales_var = safe_s7["sales_variation_vs_baseline_pct"]
    if sales_var is None:
        parts.append(
            f"sem histórico de vendas nos {safe_s7['baseline_window_days']} dias anteriores"
        )
    elif sales_var <= SALES_DROP_ALERT_THRESHOLD_PCT:
        parts.append(f"vendas {sales_var}% vs. média ({safe_s7['baseline_window_days']}d)")
    if safe_s7["open_critical_tickets"] > 0:
        parts.append(f"{safe_s7['open_critical_tickets']} ticket(s) crítico(s) aberto(s)")

    if parts:
        return "; ".join(parts)
    if signals["risk_level"] == "sem_baseline":
        return "sem histórico suficiente para comparação"
    return f"operação estável nos últimos {safe_s7['recent_window_days']} dias"

def _require_owned_producer(
    repo: RadarRepository, session_manager_id: str, producer_ref: str
) -> dict[str, Any]:
    try:
        safe_ref = sanitize_producer_ref(producer_ref)
    except (TypeError, ValueError):
        raise ProducerUnavailableError(
            f"{UNAVAILABLE_ERROR_CODE}: {UNAVAILABLE_ERROR_MESSAGE}"
        ) from None

    producer = repo.get_producer_for_manager(session_manager_id, safe_ref)
    if producer is None:
        raise ProducerUnavailableError(
            f"{UNAVAILABLE_ERROR_CODE}: {UNAVAILABLE_ERROR_MESSAGE}"
        )
    return producer


def list_at_risk_for_session(
    repo: RadarRepository,
    session_manager_id: str,
    min_score: int = DEFAULT_MIN_RISK_SCORE,
) -> dict[str, Any]:
    """Lista apenas os produtores da carteira autenticada com score >= min_score."""
    min_score = max(0, min(100, int(min_score)))
    producer_ids = repo.list_producer_ids_by_manager(session_manager_id)

    items: list[dict[str, Any]] = []
    for pid in producer_ids:
        raw = compute_producer_signals(repo, pid)
        if raw["risk_score"] >= min_score:
            safe_s7 = _sanitize_signals_block(raw["signals_last_7d"])
            summary_candidate = {
                **raw,
                "main_signal": _describe_main_signal(raw, safe_s7),
            }
            items.append(_filter_allowlist(summary_candidate, ALLOWED_SUMMARY_FIELDS))

    items.sort(key=lambda item: item["risk_score"], reverse=True)
    return {
        "session_manager_id": session_manager_id,
        "min_score_filter": min_score,
        "recent_window_days": RECENT_WINDOW_DAYS,
        "baseline_window_days": BASELINE_WINDOW_DAYS,
        "total_portfolio_producers": len(producer_ids),
        "flagged_count": len(items),
        "producers": items,
        "data_notice": "Dados simulados (seed=42). Retorno minimizado via Allowlist.",
    }


def get_signals_for_session(
    repo: RadarRepository,
    session_manager_id: str,
    producer_ref: str,
) -> dict[str, Any]:
    """Retorna os sinais detalhados de um produtor, validando posse e Allowlist."""
    producer = _require_owned_producer(repo, session_manager_id, producer_ref)
    raw = compute_producer_signals(repo, producer["id"])

    safe_detail = _filter_allowlist(raw, ALLOWED_DETAIL_FIELDS)
    safe_detail["signals_last_7d"] = _sanitize_signals_block(raw["signals_last_7d"])
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