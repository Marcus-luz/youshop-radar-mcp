"""Motor determinístico de sinais e score de risco (0 a 100).

Nota de calibração: pesos (40% webhook, 40% vendas, 20% tickets) e limiares
são hipóteses iniciais a calibrar em piloto com histórico real. A sazonalidade
neste protótipo é uma flag simulada por produtor (não inferida de série anual).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from youshop_radar.config import (
    AUXILIARY_WEBHOOK_ERRORS,
    BASELINE_WINDOW_DAYS,
    CRITICAL_TICKET_CATEGORIES,
    CRITICAL_TICKETS_SCALE_CAP,
    CRITICAL_WEBHOOK_ALERT_THRESHOLD_PCT,
    EARLY_TECH_ALERT_MIN_SCORE,
    EARLY_TECH_IMPACT_BONUS_CAP,
    FALSE_ALARM_MAX_SCORE,
    RECENT_WINDOW_DAYS,
    REFERENCE_DATE,
    RISK_HIGH_THRESHOLD,
    RISK_MEDIUM_THRESHOLD,
    SALES_DROP_SCALE_CAP_PCT,
    SALES_STABLE_FLOOR_PCT,
    SEASONAL_DIP_MAX_SCORE,
    SEASONAL_WEBHOOK_MAX_PCT,
    SEVERE_SALES_DROP_MIN_SCORE,
    SEVERE_SALES_DROP_THRESHOLD_PCT,
    SEVERE_TICKETS_MIN_SCORE,
    SEVERE_TICKETS_THRESHOLD,
    WEBHOOK_ALERT_THRESHOLD_PCT,
    WEBHOOK_SCALE_CAP_PCT,
    WEIGHT_SALES,
    WEIGHT_TICKETS,
    WEIGHT_WEBHOOK,
)
from youshop_radar.data.repository import RadarRepository

CALIBRATION_NOTE = (
    "Score calculado por regra determinística (40% webhook, 40% vendas, 20% tickets). "
    "Pesos, limiares e flag de sazonalidade são hipóteses simuladas para calibração em piloto."
)


def _select_top_webhook_error(errors_by_category: dict[str, int]) -> str:
    """Seleciona o erro dominante priorizando erros não-auxiliares em caso de empate."""
    if not errors_by_category:
        return "nenhum"
    ranked = sorted(
        errors_by_category.items(),
        key=lambda item: (
            -item[1],
            1 if item[0] in AUXILIARY_WEBHOOK_ERRORS else 0,
            item[0],
        ),
    )
    return ranked[0][0]


def evaluate_risk(
    *,
    webhook_failure_rate_pct: float,
    errors_by_category: dict[str, int],
    sales_variation_pct: float | None,
    open_critical_tickets: int,
    seasonal_pattern: bool,
) -> tuple[int, str]:
    """Calcula `(risk_score, risk_level)` de forma pura a partir dos sinais agregados."""
    failed_wh = sum(errors_by_category.values())
    non_auxiliary_failed = sum(
        qty for err, qty in errors_by_category.items() if err not in AUXILIARY_WEBHOOK_ERRORS
    )
    critical_webhook_rate_pct = (
        (webhook_failure_rate_pct * non_auxiliary_failed) / failed_wh if failed_wh > 0 else 0.0
    )
    baseline_status = "ok" if sales_variation_pct is not None else "sem_baseline"

    webhook_subscore = min(100.0, (webhook_failure_rate_pct / WEBHOOK_SCALE_CAP_PCT) * 100.0)
    sales_drop_pct = max(0.0, -sales_variation_pct) if sales_variation_pct is not None else 0.0
    sales_subscore = min(100.0, (sales_drop_pct / SALES_DROP_SCALE_CAP_PCT) * 100.0)
    ticket_subscore = min(100.0, (open_critical_tickets / CRITICAL_TICKETS_SCALE_CAP) * 100.0)

    raw_score = round(
        WEIGHT_WEBHOOK * webhook_subscore
        + WEIGHT_SALES * sales_subscore
        + WEIGHT_TICKETS * ticket_subscore
    )

    sales_is_stable = (
        sales_variation_pct is not None and sales_variation_pct >= SALES_STABLE_FLOOR_PCT
    )
    high_webhook_fail = webhook_failure_rate_pct >= WEBHOOK_ALERT_THRESHOLD_PCT
    has_critical_or_unknown_webhook_alert = (
        critical_webhook_rate_pct >= CRITICAL_WEBHOOK_ALERT_THRESHOLD_PCT
    )

    is_technical_false_alarm = (
        high_webhook_fail
        and failed_wh > 0
        and not has_critical_or_unknown_webhook_alert
        and sales_is_stable
        and open_critical_tickets == 0
    )
    is_seasonal_dip = (
        bool(seasonal_pattern)
        and baseline_status == "ok"
        and webhook_failure_rate_pct < SEASONAL_WEBHOOK_MAX_PCT
        and open_critical_tickets == 0
    )

    severe_floor = 0
    if (
        sales_variation_pct is not None
        and sales_variation_pct <= SEVERE_SALES_DROP_THRESHOLD_PCT
        and not seasonal_pattern
    ):
        severe_floor = max(severe_floor, SEVERE_SALES_DROP_MIN_SCORE)
    if open_critical_tickets >= SEVERE_TICKETS_THRESHOLD:
        severe_floor = max(severe_floor, SEVERE_TICKETS_MIN_SCORE)

    if is_technical_false_alarm:
        risk_score = min(raw_score, FALSE_ALARM_MAX_SCORE)
        risk_level = "falso_alarme_tecnico"
    elif has_critical_or_unknown_webhook_alert:
        impact_bonus = min(
            EARLY_TECH_IMPACT_BONUS_CAP,
            round(WEIGHT_SALES * sales_subscore + WEIGHT_TICKETS * ticket_subscore),
        )
        risk_score = max(raw_score, EARLY_TECH_ALERT_MIN_SCORE + impact_bonus, severe_floor)
        if risk_score >= RISK_HIGH_THRESHOLD:
            risk_level = "alto"
        elif (sales_is_stable or baseline_status == "sem_baseline") and open_critical_tickets == 0:
            risk_level = "alerta_tecnico_precoce"
        else:
            risk_level = "medio"
    elif is_seasonal_dip:
        risk_score = min(raw_score, SEASONAL_DIP_MAX_SCORE)
        risk_level = "baixo_sazonalidade"
    else:
        effective_score = max(raw_score, severe_floor)
        if baseline_status == "sem_baseline" and effective_score < RISK_MEDIUM_THRESHOLD:
            risk_score = effective_score
            risk_level = "sem_baseline"
        else:
            risk_score = effective_score
            if risk_score >= RISK_HIGH_THRESHOLD:
                risk_level = "alto"
            elif risk_score >= RISK_MEDIUM_THRESHOLD:
                risk_level = "medio"
            else:
                risk_level = "baixo"

    return max(0, min(100, int(risk_score))), risk_level

def compute_producer_signals(repo: RadarRepository, producer_ref: str) -> dict[str, Any]:
    """Calcula os sinais dos últimos 7 dias contra o baseline de 21 dias."""
    producer = repo.get_producer(producer_ref)
    if producer is None:
        raise LookupError(f"Produtor não encontrado: {producer_ref}")

    producer_id = producer["id"]
    recent_start = (REFERENCE_DATE - timedelta(days=RECENT_WINDOW_DAYS - 1)).isoformat()
    ref_end_exclusive = (REFERENCE_DATE + timedelta(days=1)).isoformat()
    baseline_start = (
        REFERENCE_DATE - timedelta(days=RECENT_WINDOW_DAYS + BASELINE_WINDOW_DAYS - 1)
    ).isoformat()
    baseline_end_exclusive = recent_start

    recent_sales = repo.get_sales_summary(producer_id, recent_start, ref_end_exclusive)
    baseline_sales = repo.get_sales_summary(producer_id, baseline_start, baseline_end_exclusive)

    baseline_weeks = BASELINE_WINDOW_DAYS / RECENT_WINDOW_DAYS
    baseline_weekly_avg = baseline_sales["revenue_brl"] / baseline_weeks
    if baseline_weekly_avg > 0:
        sales_variation_pct: float | None = round(
            ((recent_sales["revenue_brl"] - baseline_weekly_avg) / baseline_weekly_avg) * 100.0,
            1,
        )
        baseline_status = "ok"
    else:
        sales_variation_pct = None
        baseline_status = "sem_baseline"

    wh = repo.get_webhook_summary(producer_id, recent_start, ref_end_exclusive)
    total_wh = wh["total"]
    failed_wh = wh["failed"]
    errors_by_category: dict[str, int] = wh["errors_by_category"]
    top_error = _select_top_webhook_error(errors_by_category)
    webhook_failure_rate_pct = round((failed_wh / total_wh) * 100.0, 1) if total_wh > 0 else 0.0

    tickets_by_category = repo.get_open_tickets_by_category(producer_id, ref_end_exclusive)
    open_tickets_total = sum(tickets_by_category.values())
    open_critical_tickets = sum(
        qty for cat, qty in tickets_by_category.items() if cat in CRITICAL_TICKET_CATEGORIES
    )

    risk_score, risk_level = evaluate_risk(
        webhook_failure_rate_pct=webhook_failure_rate_pct,
        errors_by_category=errors_by_category,
        sales_variation_pct=sales_variation_pct,
        open_critical_tickets=open_critical_tickets,
        seasonal_pattern=bool(producer["seasonal_pattern"]),
    )

    return {
        "producer_id": producer_id,
        "manager_id": producer["manager_id"],
        "producer_name": producer["name"],
        "category": producer["category"],
        "active_creators_count": int(producer["active_creators_count"]),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "signals_last_7d": {
            "recent_window_days": RECENT_WINDOW_DAYS,
            "baseline_window_days": BASELINE_WINDOW_DAYS,
            "webhook_failure_rate_pct": webhook_failure_rate_pct,
            "webhook_failed_events": failed_wh,
            "webhook_total_events": total_wh,
            "top_webhook_error": top_error,
            "webhook_errors_by_category": errors_by_category,
            "sales_variation_vs_baseline_pct": sales_variation_pct,
            "baseline_status": baseline_status,
            "recent_orders_count": recent_sales["orders_count"],
            "open_tickets_total": open_tickets_total,
            "open_critical_tickets": open_critical_tickets,
            "tickets_by_category": tickets_by_category,
            "known_seasonal_pattern": bool(producer["seasonal_pattern"]),
        },
        "calibration_note": CALIBRATION_NOTE,
    }


def compute_weekly_history(
    repo: RadarRepository, producer_ref: str, weeks: int = 4
) -> list[dict[str, Any]]:
    """Calcula a evolução semanal (1 a 4 semanas) de vendas, webhooks e tickets."""
    producer = repo.get_producer(producer_ref)
    if producer is None:
        raise LookupError(f"Produtor não encontrado: {producer_ref}")

    producer_id = producer["id"]
    clamped_weeks = max(1, min(4, weeks))
    history: list[dict[str, Any]] = []

    for week_offset in range(clamped_weeks - 1, -1, -1):
        end_day = REFERENCE_DATE - timedelta(days=week_offset * 7)
        start_day = end_day - timedelta(days=6)
        start_iso = start_day.isoformat()
        end_exclusive_iso = (end_day + timedelta(days=1)).isoformat()

        sales = repo.get_sales_summary(producer_id, start_iso, end_exclusive_iso)
        wh = repo.get_webhook_summary(producer_id, start_iso, end_exclusive_iso)
        tickets_opened = repo.count_tickets_opened(producer_id, start_iso, end_exclusive_iso)

        wh_rate = round((wh["failed"] / wh["total"]) * 100.0, 1) if wh["total"] > 0 else 0.0
        history.append(
            {
                "week_range": f"{start_iso} a {end_day.isoformat()}",
                "orders_count": sales["orders_count"],
                "revenue_brl": round(sales["revenue_brl"], 2),
                "webhook_failure_rate_pct": wh_rate,
                "tickets_opened": tickets_opened,
            }
        )

    return history


if __name__ == "__main__":
    repository = RadarRepository()
    header = (
        f"{'PRODUTOR':<22} | {'GERENTE':<10} | {'SCORE':>5} | "
        f"{'NÍVEL':<23} | {'WH%':>5} | {'VENDAS%':>7}"
    )
    print(header)
    print("-" * len(header))
    for mgr in ("mgr_ana", "mgr_carlos"):
        for pid in repository.list_producer_ids_by_manager(mgr):
            sig = compute_producer_signals(repository, pid)
            s7 = sig["signals_last_7d"]
            var = s7["sales_variation_vs_baseline_pct"]
            var_str = f"{var:+7.1f}" if var is not None else "    N/A"
            print(
                f"{sig['producer_name']:<22} | {sig['manager_id']:<10} | "
                f"{sig['risk_score']:>5} | {sig['risk_level']:<23} | "
                f"{s7['webhook_failure_rate_pct']:>5.1f} | {var_str}"
            )