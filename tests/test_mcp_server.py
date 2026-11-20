"""Testes de contrato e E2E (stdio) do servidor MCP."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import youshop_radar.mcp_server.server as server_module
from youshop_radar.data.repository import RadarRepository
from youshop_radar.domain.security import UNAVAILABLE_ERROR_CODE
from youshop_radar.mcp_server.playbooks import RETENTION_PLAYBOOK_URI
from youshop_radar.mcp_server.server import (
    draft_retention_outreach,
    get_producer_signals,
    get_retention_playbook,
    get_signal_history,
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

    for tool in tools:
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", {})
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        assert "manager_id" not in properties

    resource_uris = {str(res.uri) for res in resources}
    assert RETENTION_PLAYBOOK_URI in resource_uris

    prompt_names = {prompt.name for prompt in prompts}
    assert "draft_retention_outreach" in prompt_names


def test_mcp_tools_use_isolated_repo_and_unified_errors(
    monkeypatch: pytest.MonkeyPatch, repo: RadarRepository
) -> None:
    monkeypatch.setenv("SESSION_MANAGER_ID", "mgr_ana")
    monkeypatch.setattr(server_module, "_repository", repo)

    risk_list = list_at_risk_producers(min_score=50)
    names = [p["producer_name"] for p in risk_list["producers"]]
    assert names == ["Aurora Cosméticos", "NutriMax Suplementos"]

    # Anti-enumeração: carteira alheia e produtor inexistente devolvem exatamente o mesmo erro
    denied_other = get_producer_signals("BellaSuplementos")
    denied_missing = get_producer_signals("EmpresaInexistente")
    assert denied_other["error"] == UNAVAILABLE_ERROR_CODE
    assert denied_other["error"] == denied_missing["error"]
    assert denied_other["message"] == denied_missing["message"]

    # Teste explícito de get_signal_history pela camada do servidor MCP
    history = get_signal_history("Aurora Cosméticos", weeks=4)
    assert history["weeks_analyzed"] == 4
    assert len(history["weekly_evolution"]) == 4

    playbook = get_retention_playbook()
    assert "Human-in-the-Loop" in playbook

    prompt_text = draft_retention_outreach("Aurora Cosméticos", channel="whatsapp")
    assert "REVISÃO E ENVIO HUMANO OBRIGATÓRIOS" in prompt_text

    with pytest.raises(ValueError, match="Canal inválido"):
        draft_retention_outreach("Aurora Cosméticos", channel="sinal_de_fumaca")


def test_mcp_server_e2e_via_stdio(seeded_db_path: Path) -> None:
    """Sobe o servidor MCP real via stdio e executa chamadas de ponta a ponta."""

    async def _run() -> dict:
        env = dict(os.environ)
        env["SESSION_MANAGER_ID"] = "mgr_ana"
        env["RADAR_DB_PATH"] = str(seeded_db_path)

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "youshop_radar.mcp_server.server"],
            env=env,
        )
        async with (
            stdio_client(params) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools_res = await session.list_tools()
            assert {t.name for t in tools_res.tools} == {
                "list_at_risk_producers",
                "get_producer_signals",
                "get_signal_history",
            }
            call_res = await session.call_tool("list_at_risk_producers", arguments={"min_score": 50})
            text = "\n".join(getattr(b, "text", "") for b in call_res.content)
            return json.loads(text)

    payload = asyncio.run(_run())
    names = [p["producer_name"] for p in payload["producers"]]
    assert names == ["Aurora Cosméticos", "NutriMax Suplementos"]