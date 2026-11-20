"""Testes da camada de domínio: motor determinístico de score."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from youshop_radar.config import BASELINE_WINDOW_DAYS, RECENT_WINDOW_DAYS, REFERENCE_DATE
from youshop_radar.data.repository import RadarRepository
from youshop_radar.domain.scoring import compute_producer_signals, compute_weekly_history


def test_score_always_between_0_and_100(repo: RadarRepository) -> None:
    for manager_id in ("mgr_ana", "mgr_carlos"):
        for pid in repo.list_producer_ids_by_manager(manager_id):
            result = compute_producer_signals(repo, pid)
            assert 0 <= result["risk_score"] <= 100


def test_risk_ordering_matches_planned_scenarios(repo: RadarRepository) -> None:
    aurora = compute_producer_signals(repo, "prod_aurora")
    nutrimax = compute_producer_signals(repo, "prod_nutrimax")
    vitapulse = compute_producer_signals(repo, "prod_vitapulse")
    codemaster = compute_producer_signals(repo, "prod_codemaster")
    gestaopro = compute_producer_signals(repo, "prod_gestaopro")
    essencia = compute_producer_signals(repo, "prod_essencia")

    assert aurora["risk_level"] == "alto"
    assert nutrimax["risk_level"] == "alto"
    assert vitapulse["risk_level"] == "alerta_tecnico_precoce"
    assert codemaster["risk_level"] == "falso_alarme_tecnico"
    assert gestaopro["risk_level"] == "baixo_sazonalidade"
    assert essencia["risk_level"] == "baixo"

    assert (
        aurora["risk_score"]
        > nutrimax["risk_score"]
        > vitapulse["risk_score"]
        > codemaster["risk_score"]
    )
    assert vitapulse["risk_score"] >= 50
    assert codemaster["risk_score"] < 50
    assert gestaopro["risk_score"] < 50

    assert aurora["signals_last_7d"]["recent_window_days"] == RECENT_WINDOW_DAYS
    assert aurora["signals_last_7d"]["baseline_window_days"] == BASELINE_WINDOW_DAYS


def test_weekly_history_returns_requested_weeks(repo: RadarRepository) -> None:
    history = compute_weekly_history(repo, "prod_aurora", weeks=4)
    assert len(history) == 4
    assert history[-1]["webhook_failure_rate_pct"] > 0
    assert history[0]["webhook_failure_rate_pct"] == 0
    assert history[-1]["orders_count"] < history[0]["orders_count"]


def test_critical_webhook_error_with_stable_sales_is_early_alert(fresh_db_path: Path) -> None:
    """Erro crítico (assinatura_invalida) com vendas estáveis NÃO pode virar falso alarme."""
    with closing(sqlite3.connect(fresh_db_path)) as conn:
        day_iso = REFERENCE_DATE.isoformat()
        conn.executemany(
            "INSERT INTO webhook_events (producer_id, created_at, status_code, error_category) "
            "VALUES (?, ?, ?, ?)",
            [("prod_essencia", day_iso, 401, "assinatura_invalida")] * 27,
        )
        conn.commit()

    repo = RadarRepository(fresh_db_path)
    result = compute_producer_signals(repo, "prod_essencia")
    assert result["signals_last_7d"]["webhook_failure_rate_pct"] >= 15.0
    assert result["risk_level"] == "alerta_tecnico_precoce"
    assert result["risk_score"] >= 55


def test_mixed_auxiliary_and_critical_errors_never_become_false_alarm(fresh_db_path: Path) -> None:
    """27 falhas auxiliares + 20 críticas não podem ser rebaixadas para falso alarme."""
    with closing(sqlite3.connect(fresh_db_path)) as conn:
        day_iso = REFERENCE_DATE.isoformat()
        conn.executemany(
            "INSERT INTO webhook_events (producer_id, created_at, status_code, error_category) "
            "VALUES (?, ?, ?, ?)",
            [("prod_essencia", day_iso, 500, "endpoint_analytics_offline")] * 27
            + [("prod_essencia", day_iso, 401, "assinatura_invalida")] * 20,
        )
        conn.commit()

    repo = RadarRepository(fresh_db_path)
    result = compute_producer_signals(repo, "prod_essencia")
    assert result["risk_level"] == "alerta_tecnico_precoce"
    assert result["risk_score"] >= 55
    assert result["signals_last_7d"]["webhook_errors_by_category"]["assinatura_invalida"] == 20


def test_critical_webhook_with_open_ticket_scores_higher_than_without(fresh_db_path: Path) -> None:
    """Falha crítica + 1 ticket aberto nunca pode ter visibilidade menor que 0 tickets."""
    repo = RadarRepository(fresh_db_path)
    without_ticket = compute_producer_signals(repo, "prod_vitapulse")

    with closing(sqlite3.connect(fresh_db_path)) as conn:
        conn.execute(
            "INSERT INTO tickets (producer_id, created_at, category, status) VALUES (?, ?, ?, ?)",
            ("prod_vitapulse", REFERENCE_DATE.isoformat(), "erro_integracao", "open"),
        )
        conn.commit()

    with_ticket = compute_producer_signals(repo, "prod_vitapulse")
    assert with_ticket["risk_score"] > without_ticket["risk_score"]
    assert with_ticket["risk_level"] in {"medio", "alto"}


def test_critical_webhook_with_mild_sales_drop_stays_flagged(fresh_db_path: Path) -> None:
    """Falha crítica + queda leve de vendas (ex.: abaixo de -5%) não pode cair para baixo."""
    with closing(sqlite3.connect(fresh_db_path)) as conn:
        # Remove 6 pedidos recentes da prod_vitapulse (~ -7% em relação ao baseline)
        recent_start = (REFERENCE_DATE - timedelta(days=6)).isoformat()
        conn.execute(
            """
            DELETE FROM orders
            WHERE id IN (
                SELECT id FROM orders
                WHERE producer_id = 'prod_vitapulse' AND created_at >= ?
                LIMIT 6
            )
            """,
            (recent_start,),
        )
        conn.commit()

    repo = RadarRepository(fresh_db_path)
    result = compute_producer_signals(repo, "prod_vitapulse")
    assert result["signals_last_7d"]["sales_variation_vs_baseline_pct"] < -5.0
    assert result["risk_score"] >= 55
    assert result["risk_level"] in {"medio", "alto"}


def test_unknown_webhook_error_category_is_treated_as_critical(fresh_db_path: Path) -> None:
    """Falhas de categoria desconhecida (>=15%) são tratadas como críticas por cautela."""
    with closing(sqlite3.connect(fresh_db_path)) as conn:
        day_iso = REFERENCE_DATE.isoformat()
        conn.executemany(
            "INSERT INTO webhook_events (producer_id, created_at, status_code, error_category) "
            "VALUES (?, ?, ?, ?)",
            [("prod_essencia", day_iso, 502, "erro_gateway_desconhecido")] * 35,
        )
        conn.commit()

    repo = RadarRepository(fresh_db_path)
    result = compute_producer_signals(repo, "prod_essencia")
    assert result["signals_last_7d"]["webhook_failure_rate_pct"] >= 20.0
    assert result["risk_level"] == "alerta_tecnico_precoce"
    assert result["risk_score"] >= 55


def test_sales_window_includes_timestamps_on_reference_day(fresh_db_path: Path) -> None:
    """Pedidos com hora no último dia (ex.: 2026-11-19T14:30) precisam entrar na contagem."""
    repo = RadarRepository(fresh_db_path)
    before = compute_producer_signals(repo, "prod_essencia")["signals_last_7d"][
        "recent_orders_count"
    ]

    with closing(sqlite3.connect(fresh_db_path)) as conn:
        conn.execute(
            "INSERT INTO orders (producer_id, created_at, amount_brl, status, buyer_email) "
            "VALUES (?, ?, ?, ?, ?)",
            ("prod_essencia", f"{REFERENCE_DATE.isoformat()}T14:30:00", 120.0, "paid", "a@b.com"),
        )
        conn.commit()

    after = compute_producer_signals(repo, "prod_essencia")["signals_last_7d"][
        "recent_orders_count"
    ]
    assert after == before + 1


def test_old_open_critical_ticket_is_still_counted(fresh_db_path: Path) -> None:
    """Ticket crítico aberto há mais de 7 dias continua contando enquanto estiver aberto."""
    old_day = (REFERENCE_DATE - timedelta(days=10)).isoformat()
    with closing(sqlite3.connect(fresh_db_path)) as conn:
        conn.execute(
            "INSERT INTO tickets (producer_id, created_at, category, status) VALUES (?, ?, ?, ?)",
            ("prod_essencia", old_day, "falha_checkout", "open"),
        )
        conn.commit()

    repo = RadarRepository(fresh_db_path)
    result = compute_producer_signals(repo, "prod_essencia")
    assert result["signals_last_7d"]["open_critical_tickets"] == 1
    assert result["signals_last_7d"]["tickets_by_category"]["falha_checkout"] == 1


def test_zero_baseline_is_reported_as_sem_baseline(fresh_db_path: Path) -> None:
    """Produtor sem histórico no baseline de 21 dias não pode parecer estável (0%)."""
    with closing(sqlite3.connect(fresh_db_path)) as conn:
        conn.execute(
            """
            INSERT INTO producers (
                id, manager_id, name, category, active_creators_count,
                seasonal_pattern, owner_email, tax_id, webhook_secret
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "prod_novo",
                "mgr_ana",
                "NovaLoja Teste",
                "cursos",
                2,
                0,
                "novo@example.com",
                "00.000.000/0001-00",
                "whsec_test_only_123",
            ),
        )
        conn.commit()

    repo = RadarRepository(fresh_db_path)
    result = compute_producer_signals(repo, "prod_novo")
    assert result["signals_last_7d"]["sales_variation_vs_baseline_pct"] is None
    assert result["signals_last_7d"]["baseline_status"] == "sem_baseline"
    assert result["risk_level"] == "sem_baseline"