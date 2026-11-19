"""Testes de segurança: isolamento de carteira (sessão) e Allowlist LGPD."""

from __future__ import annotations

import json
import sqlite3

import pytest

from youshop_radar.data.repository import RadarRepository
from youshop_radar.domain.security import (
    PortfolioAccessError,
    get_history_for_session,
    get_signals_for_session,
    list_at_risk_for_session,
)


def test_manager_only_sees_own_portfolio_in_listing(repo: RadarRepository) -> None:
    ana_list = list_at_risk_for_session(repo, session_manager_id="mgr_ana", min_score=0)
    ana_ids = {p["producer_id"] for p in ana_list["producers"]}

    assert "prod_aurora" in ana_ids
    assert "prod_nutrimax" in ana_ids
    assert "prod_bella" not in ana_ids
    assert "prod_academy" not in ana_ids


def test_manager_cannot_access_other_manager_producer(repo: RadarRepository) -> None:
    with pytest.raises(PortfolioAccessError, match="ACCESS_DENIED_PORTFOLIO"):
        get_signals_for_session(repo, session_manager_id="mgr_ana", producer_ref="prod_bella")

    with pytest.raises(PortfolioAccessError, match="ACCESS_DENIED_PORTFOLIO"):
        get_signals_for_session(repo, session_manager_id="mgr_ana", producer_ref="BellaSuplementos")

    with pytest.raises(PortfolioAccessError, match="ACCESS_DENIED_PORTFOLIO"):
        get_history_for_session(repo, session_manager_id="mgr_ana", producer_ref="prod_bella")


def test_no_pii_or_secrets_leak_in_responses(repo: RadarRepository) -> None:
    # Lê os segredos e dados pessoais reais existentes no banco simulado
    with sqlite3.connect(repo.db_path) as conn:
        sensitive_rows = conn.execute(
            "SELECT owner_email, tax_id, webhook_secret FROM producers"
        ).fetchall()
        buyer_emails = [
            row[0] for row in conn.execute("SELECT DISTINCT buyer_email FROM orders LIMIT 50")
        ]

    sensitive_values = set(buyer_emails)
    for owner_email, tax_id, webhook_secret in sensitive_rows:
        sensitive_values.update({owner_email, tax_id, webhook_secret})

    # Serializa todas as saídas permitidas para a carteira da Ana
    payloads = [
        list_at_risk_for_session(repo, "mgr_ana", min_score=0),
        get_signals_for_session(repo, "mgr_ana", "prod_aurora"),
        get_history_for_session(repo, "mgr_ana", "prod_aurora", weeks=4),
    ]
    serialized = json.dumps(payloads, ensure_ascii=False)

    # Nenhum nome de coluna sensível nem valor sensível pode aparecer
    for forbidden_key in ("owner_email", "tax_id", "webhook_secret", "buyer_email", "whsec_"):
        assert forbidden_key not in serialized

    for secret in sensitive_values:
        assert secret not in serialized