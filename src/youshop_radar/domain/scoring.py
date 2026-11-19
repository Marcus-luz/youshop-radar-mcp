"""Motor determinístico de sinais e score de risco (0 a 100).

Nota de calibração: pesos (40% webhook, 40% vendas, 20% tickets) e limiares
são hipóteses iniciais a calibrar em piloto com dados reais da YouShop.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from youshop_radar.config import (
    CRITICAL_TICKET_CATEGORIES,
    REFERENCE_DATE,
    WEIGHT_SALES,
    WEIGHT_TICKETS,
    WEIGHT_WEBHOOK,
)
from youshop_radar.data.repository import RadarRepository

CALIBRATION_NOTE = (
    "Score calculado por regra determinística (40% webhook, 40% vendas, 20% tickets). "
    "Pesos e limiares são hipóteses iniciais a calibrar em piloto."
)


def compute_producer_signals(repo: RadarRepository, producer_ref: str) -> dict[str, Any]:
    """Calcula os sinais dos últimos 7 dias contra o baseline de 21 dias."""
    producer = repo.get_producer(producer_ref)
    if producer is None:
        raise LookupError(f"Produtor não encontrado: {producer_ref}")

    producer_id = producer["id"]
    recent_start = (REFERENCE_DATE - timedelta(days=6)).isoformat()
    baseline_start = (REFERENCE_DATE - timedelta(days=27)).isoformat()
    baseline_end = (REFERENCE_DATE - timedelta(days=7)).isoformat()
    ref_iso = REFERENCE_DATE.isoformat()

    # 1. Vendas recentes (7d) vs. média semanal do baseline (21d / 3)
    recent_sales = repo.get_sales_summary(producer_id, recent_start, ref_iso)
    baseline_sales = repo.get_sales_summary(producer_id, baseline_start, baseline_end)

    baseline_weekly_avg = baseline_sales["revenue_brl"] / 3.0
    if baseline_weekly_avg > 0:
        sales_variation_pct = round(
            ((recent_sales["revenue_brl"] - baseline_weekly_avg) / baseline_weekly_avg) * 100.0,
            1,
        )
    else:
        sales_variation_pct = 0.0

    # 2. Falhas de webhook nos últimos 7 dias
    wh = repo.get_webhook_summary(producer_id, recent_start, ref_iso)
    total_wh = wh["total"]
    failed_wh = wh["failed"]
    webhook_failure_rate_pct = round((failed_wh / total_wh) * 100.0, 1) if total_wh > 0 else 0.0

    # 3. Tickets abertos por categoria nos últimos 7 dias
    tickets_by_category = repo.get_open_tickets_by_category(producer_id, recent_start, ref_iso)
    open_tickets_total = sum(tickets_by_category.values())
    open_critical_tickets = sum(
        qty for cat, qty in tickets_by_category.items() if cat in CRITICAL_TICKET_CATEGORIES
    )

    # 4. Score determinístico (0 a 100)
    webhook_subscore = min(100.0, (webhook_failure_rate_pct / 30.0) * 100.0)
    sales_drop_pct = max(0.0, -sales_variation_pct)
    sales_subscore = min(100.0, (sales_drop_pct / 40.0) * 100.0)
    ticket_subscore = min(100.0, (open_critical_tickets / 3.0) * 100.0)

    raw_score = round(
        WEIGHT_WEBHOOK * webhook_subscore
        + WEIGHT_SALES * sales_subscore
        + WEIGHT_TICKETS * ticket_subscore
    )

    # Trava 1: Falso alarme técnico (muitos erros, mas vendas estáveis e sem ticket crítico)
    is_technical_false_alarm = (
        webhook_failure_rate_pct >= 15.0
        and sales_variation_pct >= -5.0
        and open_critical_tickets == 0
    )
    # Trava 2: Queda isolada por sazonalidade (sem erro técnico nem ticket crítico)
    is_seasonal_dip = (
        bool(producer["seasonal_pattern"])
        and webhook_failure_rate_pct < 5.0
        and open_critical_tickets == 0
    )

    if is_technical_false_alarm:
        risk_score = min(raw_score, 45)
        risk_level = "falso_alarme_tecnico"
    elif is_seasonal_dip:
        risk_score = min(raw_score, 35)
        risk_level = "baixo_sazonalidade"
    else:
        risk_score = raw_score
        if risk_score >= 70:
            risk_level = "alto"
        elif risk_score >= 50:
            risk_level = "medio"
        else:
            risk_level = "baixo"

    risk_score = max(0, min(100, int(risk_score)))

    return {
        "producer_id": producer_id,
        "manager_id": producer["manager_id"],
        "producer_name": producer["name"],
        "category": producer["category"],
        "active_creators_count": int(producer["active_creators_count"]),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "signals_last_7d": {
            "webhook_failure_rate_pct": webhook_failure_rate_pct,
            "webhook_failed_events": failed_wh,
            "webhook_total_events": total_wh,
            "top_webhook_error": wh["top_error"],
            "sales_variation_vs_baseline_pct": sales_variation_pct,
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
        start_iso, end_iso = start_day.isoformat(), end_day.isoformat()

        sales = repo.get_sales_summary(producer_id, start_iso, end_iso)
        wh = repo.get_webhook_summary(producer_id, start_iso, end_iso)
        tickets_opened = repo.count_tickets_opened(producer_id, start_iso, end_iso)

        wh_rate = round((wh["failed"] / wh["total"]) * 100.0, 1) if wh["total"] > 0 else 0.0
        history.append(
            {
                "week_range": f"{start_iso} a {end_iso}",
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
        f"{'NÍVEL':<21} | {'WH%':>5} | {'VENDAS%':>7}"
    )
    print(header)
    print("-" * len(header))
    for mgr in ("mgr_ana", "mgr_carlos"):
        for pid in repository.list_producer_ids_by_manager(mgr):
            sig = compute_producer_signals(repository, pid)
            s7 = sig["signals_last_7d"]
            print(
                f"{sig['producer_name']:<22} | {sig['manager_id']:<10} | "
                f"{sig['risk_score']:>5} | {sig['risk_level']:<21} | "
                f"{s7['webhook_failure_rate_pct']:>5.1f} | "
                f"{s7['sales_variation_vs_baseline_pct']:>+7.1f}"
            )