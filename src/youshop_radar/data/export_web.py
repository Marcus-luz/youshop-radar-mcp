"""Exporta dados determinísticos e vetores de paridade para a versão web (GitHub Pages)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from youshop_radar.config import (
    AUXILIARY_WEBHOOK_ERRORS,
    BASELINE_WINDOW_DAYS,
    CRITICAL_TICKET_CATEGORIES,
    CRITICAL_TICKETS_SCALE_CAP,
    CRITICAL_WEBHOOK_ERRORS,
    EARLY_TECH_ALERT_MIN_SCORE,
    EARLY_TECH_IMPACT_BONUS_CAP,
    FALSE_ALARM_MAX_SCORE,
    PROJECT_ROOT,
    RECENT_WINDOW_DAYS,
    REFERENCE_DATE,
    RISK_HIGH_THRESHOLD,
    RISK_MEDIUM_THRESHOLD,
    SALES_DROP_SCALE_CAP_PCT,
    SALES_STABLE_FLOOR_PCT,
    SEASONAL_DIP_MAX_SCORE,
    SEASONAL_WEBHOOK_MAX_PCT,
    SEED,
    WEBHOOK_ALERT_THRESHOLD_PCT,
    WEBHOOK_SCALE_CAP_PCT,
    WEIGHT_SALES,
    WEIGHT_TICKETS,
    WEIGHT_WEBHOOK,
)
from youshop_radar.data.repository import RadarRepository
from youshop_radar.data.seed import ensure_seeded
from youshop_radar.domain.scoring import CALIBRATION_NOTE, evaluate_risk
from youshop_radar.domain.security import (
    UNAVAILABLE_ERROR_CODE,
    UNAVAILABLE_ERROR_MESSAGE,
    get_history_for_session,
    get_signals_for_session,
    list_at_risk_for_session,
)

WEB_DATA_OUTPUT_PATH = PROJECT_ROOT / "docs" / "dashboard_data.json"

MANAGERS = [
    {"id": "mgr_ana", "name": "Ana Silva", "role": "Gerente de Contas (Carteira A)"},
    {"id": "mgr_carlos", "name": "Carlos Mendes", "role": "Gerente de Contas (Carteira B)"},
]

PARITY_INPUTS: list[dict[str, Any]] = [
    {
        "id": "risco_alto_aurora",
        "label": "Risco alto (Aurora)",
        "webhook_failure_rate_pct": 32.1,
        "errors_by_category": {"assinatura_invalida": 45},
        "sales_variation_pct": -40.1,
        "open_critical_tickets": 2,
        "seasonal_pattern": False,
    },
    {
        "id": "risco_alto_nutrimax",
        "label": "Risco alto (NutriMax)",
        "webhook_failure_rate_pct": 22.9,
        "errors_by_category": {"timeout_checkout_pix": 24},
        "sales_variation_pct": -32.9,
        "open_critical_tickets": 3,
        "seasonal_pattern": False,
    },
    {
        "id": "alerta_precoce_vitapulse",
        "label": "Alerta técnico precoce (VitaPulse)",
        "webhook_failure_rate_pct": 23.8,
        "errors_by_category": {"assinatura_invalida": 25},
        "sales_variation_pct": -1.0,
        "open_critical_tickets": 0,
        "seasonal_pattern": False,
    },
    {
        "id": "falso_alarme_codemaster",
        "label": "Falso alarme técnico (CodeMaster)",
        "webhook_failure_rate_pct": 28.6,
        "errors_by_category": {"endpoint_analytics_offline": 30},
        "sales_variation_pct": 7.5,
        "open_critical_tickets": 0,
        "seasonal_pattern": False,
    },
    {
        "id": "sazonalidade_gestaopro",
        "label": "Queda sazonal simulada (GestãoPro)",
        "webhook_failure_rate_pct": 0.0,
        "errors_by_category": {},
        "sales_variation_pct": -20.4,
        "open_critical_tickets": 0,
        "seasonal_pattern": True,
    },
    {
        "id": "erros_mistos_aux_e_critico",
        "label": "Erros mistos (27 aux + 20 críticos)",
        "webhook_failure_rate_pct": 30.9,
        "errors_by_category": {"endpoint_analytics_offline": 27, "assinatura_invalida": 20},
        "sales_variation_pct": 2.0,
        "open_critical_tickets": 0,
        "seasonal_pattern": False,
    },
    {
        "id": "critico_com_ticket_aberto",
        "label": "Falha crítica + 1 ticket aberto",
        "webhook_failure_rate_pct": 20.0,
        "errors_by_category": {"assinatura_invalida": 20},
        "sales_variation_pct": 0.0,
        "open_critical_tickets": 1,
        "seasonal_pattern": False,
    },
    {
        "id": "critico_com_queda_leve",
        "label": "Falha crítica + queda leve (-5.8%)",
        "webhook_failure_rate_pct": 20.0,
        "errors_by_category": {"assinatura_invalida": 20},
        "sales_variation_pct": -5.8,
        "open_critical_tickets": 0,
        "seasonal_pattern": False,
    },
    {
        "id": "categoria_desconhecida",
        "label": "Categoria desconhecida (cautela)",
        "webhook_failure_rate_pct": 25.0,
        "errors_by_category": {"erro_nao_catalogado": 25},
        "sales_variation_pct": 0.0,
        "open_critical_tickets": 0,
        "seasonal_pattern": False,
    },
    {
        "id": "sem_baseline",
        "label": "Produtor sem histórico (sem_baseline)",
        "webhook_failure_rate_pct": 0.0,
        "errors_by_category": {},
        "sales_variation_pct": None,
        "open_critical_tickets": 0,
        "seasonal_pattern": False,
    },
    {
        "id": "saudavel",
        "label": "Operação saudável",
        "webhook_failure_rate_pct": 0.0,
        "errors_by_category": {},
        "sales_variation_pct": 8.0,
        "open_critical_tickets": 0,
        "seasonal_pattern": False,
    },
]


def build_web_payload(repo: RadarRepository) -> dict[str, Any]:
    """Constrói o JSON determinístico consumido pelo GitHub Pages."""
    portfolios: dict[str, Any] = {}
    directory: list[dict[str, str]] = []

    for mgr in MANAGERS:
        mgr_id = mgr["id"]
        summary = list_at_risk_for_session(repo, mgr_id, min_score=0)
        details: dict[str, Any] = {}

        for item in summary["producers"]:
            pid = item["producer_id"]
            details[pid] = {
                "signals": get_signals_for_session(repo, mgr_id, pid),
                "history": get_history_for_session(repo, mgr_id, pid, weeks=4),
            }
            directory.append(
                {
                    "producer_id": pid,
                    "producer_name": item["producer_name"],
                    "owner_manager_id": mgr_id,
                }
            )

        portfolios[mgr_id] = {
            "summary": summary,
            "details": details,
        }

    parity_cases: list[dict[str, Any]] = []
    for case in PARITY_INPUTS:
        score, level = evaluate_risk(
            webhook_failure_rate_pct=case["webhook_failure_rate_pct"],
            errors_by_category=case["errors_by_category"],
            sales_variation_pct=case["sales_variation_pct"],
            open_critical_tickets=case["open_critical_tickets"],
            seasonal_pattern=case["seasonal_pattern"],
        )
        parity_cases.append(
            {
                **case,
                "expected_risk_score": score,
                "expected_risk_level": level,
            }
        )

    return {
        "metadata": {
            "project": "YouShop Radar MCP",
            "seed": SEED,
            "reference_date": REFERENCE_DATE.isoformat(),
            "recent_window_days": RECENT_WINDOW_DAYS,
            "baseline_window_days": BASELINE_WINDOW_DAYS,
            "calibration_note": CALIBRATION_NOTE,
            "unavailable_error": {
                "error": UNAVAILABLE_ERROR_CODE,
                "message": UNAVAILABLE_ERROR_MESSAGE,
            },
        },
        "scoring_config": {
            "weight_webhook": WEIGHT_WEBHOOK,
            "weight_sales": WEIGHT_SALES,
            "weight_tickets": WEIGHT_TICKETS,
            "webhook_scale_cap_pct": WEBHOOK_SCALE_CAP_PCT,
            "sales_drop_scale_cap_pct": SALES_DROP_SCALE_CAP_PCT,
            "critical_tickets_scale_cap": CRITICAL_TICKETS_SCALE_CAP,
            "webhook_alert_threshold_pct": WEBHOOK_ALERT_THRESHOLD_PCT,
            "sales_stable_floor_pct": SALES_STABLE_FLOOR_PCT,
            "seasonal_webhook_max_pct": SEASONAL_WEBHOOK_MAX_PCT,
            "false_alarm_max_score": FALSE_ALARM_MAX_SCORE,
            "early_tech_alert_min_score": EARLY_TECH_ALERT_MIN_SCORE,
            "early_tech_impact_bonus_cap": EARLY_TECH_IMPACT_BONUS_CAP,
            "seasonal_dip_max_score": SEASONAL_DIP_MAX_SCORE,
            "risk_high_threshold": RISK_HIGH_THRESHOLD,
            "risk_medium_threshold": RISK_MEDIUM_THRESHOLD,
            "auxiliary_webhook_errors": sorted(AUXILIARY_WEBHOOK_ERRORS),
            "critical_webhook_errors": sorted(CRITICAL_WEBHOOK_ERRORS),
            "critical_ticket_categories": sorted(CRITICAL_TICKET_CATEGORIES),
        },
        "managers": MANAGERS,
        "portfolios": portfolios,
        "directory": directory,
        "parity_cases": parity_cases,
    }


def export_web_data(
    db_path: Path | str | None = None,
    output_path: Path | str = WEB_DATA_OUTPUT_PATH,
) -> Path:
    """Exporta docs/dashboard_data.json a partir do banco SQLite simulado."""
    if db_path is None:
        ensure_seeded()
    repo = RadarRepository(db_path)
    payload = build_web_payload(repo)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    written = export_web_data()
    print(f"Dados web exportados com sucesso em: {written}")