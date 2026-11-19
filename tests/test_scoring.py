"""Testes da camada de domínio: motor determinístico de score."""

from __future__ import annotations

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
    # Última semana precisa ter falhas de webhook e menos pedidos que a primeira
    assert history[-1]["webhook_failure_rate_pct"] > 0
    assert history[0]["webhook_failure_rate_pct"] == 0
    assert history[-1]["orders_count"] < history[0]["orders_count"]