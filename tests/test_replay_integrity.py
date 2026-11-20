"""Testes de integridade do replay publicado e do verificador numérico tipado."""

from __future__ import annotations

import json
from pathlib import Path

from demo_client.groq_client import (
    audit_recorded_step,
    verify_grounded_numbers,
    verify_scene_policies,
)
from youshop_radar.mcp_server.playbooks import RETENTION_PLAYBOOK_TEXT

REPLAY_PATH = Path(__file__).resolve().parents[1] / "docs" / "replay_data.json"


def test_typed_verifier_rejects_score_used_as_percentage() -> None:
    """93 como score NÃO pode validar '93% de falha' (sem pool único)."""
    trace = [
        {
            "tool": "get_producer_signals",
            "arguments": {"producer_ref": "Aurora Cosméticos"},
            "result": {
                "risk_score": 93,
                "signals_last_7d": {"webhook_failure_rate_pct": 32.1},
            },
        }
    ]
    check = verify_grounded_numbers("A taxa de falha foi de 93%.", trace)
    assert check["verified"] is False
    assert "pct:93" in check["ungrounded_numbers"]


def test_typed_verifier_rejects_ungrounded_time_units() -> None:
    """Prazos inventados (ex.: '48 horas') precisam falhar na auditoria."""
    check = verify_grounded_numbers(
        "Monitore nas próximas 48 horas e agende chamada de 10 minutos.",
        tool_trace=[],
        playbook_text=RETENTION_PLAYBOOK_TEXT,
    )
    assert check["verified"] is False
    assert "time_hours:48h" in check["ungrounded_numbers"]
    assert "time_minutes:10min" not in check["ungrounded_numbers"]


def test_scene_policy_catches_forbidden_jargon_and_workarounds() -> None:
    bad_draft = verify_scene_policies(
        "rascunho_contato",
        "[RASCUNHO — REVISÃO E ENVIO HUMANO OBRIGATÓRIOS] Queda vs. baseline nas últimas 7 dias.",
    )
    assert bad_draft["passed"] is False
    assert "proibido_na_cena:baseline" in bad_draft["violations"]
    assert "proibido_na_cena:últimas 7 dias" in bad_draft["violations"]

    bad_refusal = verify_scene_policies(
        "recusa_carteira",
        "Não disponível. Você pode abrir ticket para acesso temporário.",
    )
    assert bad_refusal["passed"] is False


def test_recorded_replay_passes_full_audit() -> None:
    """Garante no CI que todas as cenas gravadas em docs/replay_data.json estão íntegras."""
    payload = json.loads(REPLAY_PATH.read_text(encoding="utf-8"))
    steps = payload["steps"]
    assert len(steps) == 5

    for step in steps:
        grounding, policy = audit_recorded_step(step, playbook_text=RETENTION_PLAYBOOK_TEXT)
        assert grounding["verified"] is True, (
            f"Falha numérica na cena '{step['id']}': {grounding['ungrounded_numbers']}"
        )
        assert policy["passed"] is True, (
            f"Falha de política na cena '{step['id']}': {policy['violations']}"
        )