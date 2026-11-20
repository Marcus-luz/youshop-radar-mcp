"""Testes da camada de domínio: motor determinístico de score."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from youshop_radar.config import REFERENCE_DATE
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
    codemaster = compute_producer_signals(repo, "prod_codemaster")
    gestaopro = compute_producer_signals(repo, "prod_gestaopro")
    essencia = compute_producer_signals(repo, "prod_essencia")

    assert aurora["risk_level"] == "alto"
    assert nutrimax["risk_level"] == "alto"
    assert codemaster["risk_level"] == "falso_alarme_tecnico"
    assert gestaopro["risk_level"] == "baixo_sazonalidade"
    assert essencia["risk_level"] == "baixo"

    assert aurora["risk_score"] > nutrimax["risk_score"] > codemaster["risk_score"]
    assert codemaster["risk_score"] < 50
    assert gestaopro["risk_score"] < 50


def test_weekly_history_returns_requested_weeks(repo: RadarRepository) -> None:
    history = compute_weekly_history(repo, "prod_aurora", weeks=4)
    assert len(history) == 4
    assert history[-1]["webhook_failure_rate_pct"] > 0
    assert history[0]["webhook_failure_rate_pct"] == 0
    assert history[-1]["orders_count"] < history[0]["orders_count"]


def test_critical_webhook_error_with_stable_sales_is_early_alert(fresh_db_path: Path) -> None:
    """Erro crítico (assinatura_invalida) com vendas estáveis NÃO pode virar falso alarme."""
    with closing(sqlite3.connect(fresh_db_path)) as conn:
        # prod_essencia tem vendas estáveis (+8%) e 0 tickets; inserimos 20.5% de falha crítica
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
    assert result["risk_score"] >= 50


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