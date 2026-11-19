"""Testes de contrato do servidor MCP (3 Tools, 1 Resource, 1 Prompt)."""

from __future__ import annotations

import asyncio

from youshop_radar.mcp_server.playbooks import RETENTION_PLAYBOOK_URI
from youshop_radar.mcp_server.server import (
    draft_retention_outreach,
    get_producer_signals,
    get_retention_playbook,
    list_at_risk_producers,
    mcp,
)


def test_mcp_exposes_exact_primitives() -> None:
    tools = asyncio.run(mcp.list_tools())
    resources = asyncio.run(mcp.list_resources())
    prompts = asyncio.run(mcp.list_prompts())

    tool_names = {tool.name for tool in tools}
    assert tool_names == {
        "list_at_risk_producers",
        "get_producer_signals",
        "get_signal_history",
    }

    # Nenhuma tool pode aceitar manager_id escolhido pelo modelo de IA
    for tool in tools:
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {})
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        assert "manager_id" not in properties

    resource_uris = {str(res.uri) for res in resources}
    assert RETENTION_PLAYBOOK_URI in resource_uris

    prompt_names = {prompt.name for prompt in prompts}
    assert "draft_retention_outreach" in prompt_names


def test_mcp_tools_respect_session_and_return_clean_errors(monkeypatch) -> None:
    monkeypatch.setenv("SESSION_MANAGER_ID", "mgr_ana")

    risk_list = list_at_risk_producers(min_score=50)
    names = [p["producer_name"] for p in risk_list["producers"]]
    assert names == ["Aurora Cosméticos", "NutriMax Suplementos"]

    denied = get_producer_signals("BellaSuplementos")
    assert denied["error"] == "ACCESS_DENIED_PORTFOLIO"

    playbook = get_retention_playbook()
    assert "Human-in-the-Loop" in playbook

    prompt_text = draft_retention_outreach("Aurora Cosméticos", channel="whatsapp")
    assert "REVISÃO E ENVIO HUMANO OBRIGATÓRIOS" in prompt_text