"""Cliente MCP local conectado via stdio à API gratuita do Groq.

Totalmente desacoplado do servidor: não importa nenhum módulo de `youshop_radar`,
descobrindo Tools, Resources e Prompts dinamicamente pelo protocolo MCP.

Modos de uso:
  uv run python demo_client/groq_client.py --record       # Grava docs/replay_data.json
  uv run python demo_client/groq_client.py --interactive  # Chat interativo no terminal
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import Groq
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from rich.console import Console
from rich.panel import Panel

load_dotenv()

MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPLAY_OUTPUT_PATH = PROJECT_ROOT / "docs" / "replay_data.json"

console = Console()

DEMO_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "priorizacao",
        "title": "1. Priorização da carteira",
        "badge": "Visão Comercial",
        "mode": "question",
        "user_message": (
            "Quem da minha carteira precisa de atenção hoje? "
            "Liste por prioridade e explique em linguagem simples o motivo."
        ),
    },
    {
        "id": "diagnostico_aurora",
        "title": "2. Diagnóstico + evolução semanal",
        "badge": "Cruzamento de Áreas",
        "mode": "question",
        "user_message": (
            "Detalhe os sinais da Aurora Cosméticos e mostre a evolução semanal "
            "para eu saber desde quando o problema começou."
        ),
    },
    {
        "id": "rascunho_contato",
        "title": "3. Rascunho de abordagem (HITL)",
        "badge": "MCP Prompt + Resource",
        "mode": "mcp_prompt",
        "prompt_name": "draft_retention_outreach",
        "prompt_args": {"producer_ref": "Aurora Cosméticos", "channel": "whatsapp"},
        "user_message": (
            "Gere um rascunho de mensagem no WhatsApp para a Aurora Cosméticos "
            "seguindo o playbook de retenção da YouShop (sem enviar)."
        ),
    },
    {
        "id": "recusa_carteira",
        "title": "4. Segurança: recusa de carteira alheia",
        "badge": "Isolamento de Sessão",
        "mode": "question",
        "user_message": (
            "Quero ver também os sinais da BellaSuplementos para comparar com a minha carteira."
        ),
    },
    {
        "id": "falso_alarme",
        "title": "5. Falso alarme técnico",
        "badge": "Uso Consciente da IA",
        "mode": "question",
        "user_message": (
            "Vi que a CodeMaster Cursos teve erros de webhook. "
            "Ela está em risco de churn? Devo ligar com urgência?"
        ),
    },
]


def _tool_input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}}


def _extract_text(blocks: Any) -> str:
    if isinstance(blocks, str):
        return blocks
    parts: list[str] = []
    for item in blocks or []:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(str(text))
        elif isinstance(item, dict) and "text" in item:
            parts.append(str(item["text"]))
        else:
            parts.append(str(item))
    return "\n".join(parts)


def _try_parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _build_groq_tools(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": _tool_input_schema(tool),
            },
        }
        for tool in mcp_tools
    ]


async def _run_agent_turn(
    groq_client: Groq,
    session: ClientSession,
    groq_tools: list[dict[str, Any]],
    system_prompt: str,
    user_prompt: str,
    max_rounds: int = 5,
) -> tuple[str, list[dict[str, Any]]]:
    """Executa um turno completo LLM <-> Servidor MCP e retorna (resposta, logs_de_tools)."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    tool_trace: list[dict[str, Any]] = []

    for _ in range(max_rounds):
        response = groq_client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=groq_tools,
            tool_choice="auto",
            temperature=0.1,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            return (msg.content or "").strip(), tool_trace

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            tool_name = tc.function.name
            parsed_args = _try_parse_json(tc.function.arguments or "{}")
            if not isinstance(parsed_args, dict):
                parsed_args = {}

            mcp_result = await session.call_tool(tool_name, arguments=parsed_args)
            result_text = _extract_text(getattr(mcp_result, "content", []))
            parsed_result = _try_parse_json(result_text)

            tool_trace.append(
                {
                    "tool": tool_name,
                    "arguments": parsed_args,
                    "result": parsed_result,
                }
            )

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                }
            )

    return "Limite de chamadas de ferramenta atingido nesta rodada.", tool_trace


async def _load_playbook_resource(session: ClientSession) -> tuple[str, str]:
    resources_res = await session.list_resources()
    resources = getattr(resources_res, "resources", [])
    if not resources:
        return "", ""
    uri = str(resources[0].uri)
    read_res = await session.read_resource(resources[0].uri)
    text = _extract_text(getattr(read_res, "contents", []))
    return uri, text


async def _build_system_prompt(session: ClientSession) -> tuple[str, str]:
    playbook_uri, playbook_text = await _load_playbook_resource(session)
    system_prompt = (
        "Você é o YouShop Radar, um agente analítico somente leitura conectado via MCP "
        "para apoiar gerentes de contas do Comercial da YouShop.\n"
        "Regras obrigatórias:\n"
        "- Sempre consulte as ferramentas MCP antes de responder; nunca invente números.\n"
        "- Cite os sinais medidos retornados pelo servidor (score, taxa de webhook, variação de "
        "vendas, tickets e creators ativos).\n"
        "- Se o servidor retornar ACCESS_DENIED_PORTFOLIO, informe claramente que o acesso foi "
        "recusado por isolamento de carteira da sessão e não tente contornar.\n"
        "- Se o nível for `falso_alarme_tecnico`, destaque explicitamente o que os dados NÃO "
        "comprovam (erro técnico sem queda de vendas) e recomende não tratar como churn urgente.\n"
        "- Responda sempre em português do Brasil, de forma objetiva e clara para não técnicos.\n\n"
        f"Playbook carregado do MCP Resource ({playbook_uri}):\n{playbook_text}"
    )
    return playbook_uri, system_prompt


def _server_params() -> StdioServerParameters:
    env = dict(os.environ)
    env.setdefault("SESSION_MANAGER_ID", "mgr_ana")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "youshop_radar.mcp_server.server"],
        env=env,
    )

async def run_recorded_demo(groq_client: Groq) -> None:
    """Executa o roteiro de 5 cenas, mostra no terminal e grava docs/replay_data.json."""
    session_manager = os.getenv("SESSION_MANAGER_ID", "mgr_ana")

    async with (
        stdio_client(_server_params()) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()

        tools_res = await session.list_tools()
        mcp_tools = getattr(tools_res, "tools", [])
        prompts_res = await session.list_prompts()
        mcp_prompts = getattr(prompts_res, "prompts", [])

        playbook_uri, system_prompt = await _build_system_prompt(session)
        groq_tools = _build_groq_tools(mcp_tools)

        console.print(
            Panel.fit(
                f"[bold green]Conectado ao servidor YouShop Radar MCP (stdio)[/bold green]\n"
                f"Sessão: [cyan]{session_manager}[/cyan] | Modelo: [cyan]{MODEL_NAME}[/cyan]\n"
                f"Tools: {[t.name for t in mcp_tools]}\n"
                f"Resource: {playbook_uri} | Prompts: {[p.name for p in mcp_prompts]}",
                title="YouShop Radar — Sessão Real (Dados Simulados)",
            )
        )

        recorded_steps: list[dict[str, Any]] = []

        for scenario in DEMO_SCENARIOS:
            console.rule(f"[bold]{scenario['title']}[/bold]")
            console.print(
                f"[bold cyan]Gerente ({session_manager}):[/bold cyan] "
                f"{scenario['user_message']}"
            )

            mcp_prompt_used: dict[str, Any] | None = None
            effective_prompt = scenario["user_message"]

            if scenario["mode"] == "mcp_prompt":
                prompt_res = await session.get_prompt(
                    scenario["prompt_name"],
                    arguments=scenario["prompt_args"],
                )
                prompt_messages = getattr(prompt_res, "messages", [])
                extracted_parts = [
                    _extract_text([getattr(m, "content", "")]) for m in prompt_messages
                ]
                effective_prompt = "\n".join(extracted_parts).strip() or effective_prompt
                mcp_prompt_used = {
                    "name": scenario["prompt_name"],
                    "arguments": scenario["prompt_args"],
                    "rendered_instruction": effective_prompt,
                }

            answer, tool_trace = await _run_agent_turn(
                groq_client=groq_client,
                session=session,
                groq_tools=groq_tools,
                system_prompt=system_prompt,
                user_prompt=effective_prompt,
            )

            for call in tool_trace:
                args_str = json.dumps(call["arguments"], ensure_ascii=False)
                console.print(f"  [dim]→ MCP Tool: {call['tool']}({args_str})[/dim]")

            console.print(
                Panel(answer, title=f"YouShop Radar ({MODEL_NAME} + MCP)", border_style="green")
            )

            recorded_steps.append(
                {
                    "id": scenario["id"],
                    "title": scenario["title"],
                    "badge": scenario["badge"],
                    "user_message": scenario["user_message"],
                    "mcp_prompt_used": mcp_prompt_used,
                    "mcp_tool_calls": tool_trace,
                    "assistant_response": answer,
                }
            )

            await asyncio.sleep(0.6)

        replay_payload = {
            "metadata": {
                "project": "YouShop Radar MCP",
                "recorded_at_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
                "session_manager_id": session_manager,
                "llm_model": MODEL_NAME,
                "mcp_primitives": {
                    "tools": [t.name for t in mcp_tools],
                    "resources": [playbook_uri] if playbook_uri else [],
                    "prompts": [p.name for p in mcp_prompts],
                },
                "disclaimer": (
                    "Gravação de sessão real do servidor MCP com dados simulados (seed=42). "
                    "Nenhuma ação real é executada."
                ),
            },
            "steps": recorded_steps,
        }

        REPLAY_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPLAY_OUTPUT_PATH.write_text(
            json.dumps(replay_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        console.print(
            f"\n[bold green]Sessão gravada com sucesso em:[/bold green] {REPLAY_OUTPUT_PATH}"
        )


async def run_interactive_chat(groq_client: Groq) -> None:
    """Abre um chat interativo ao vivo no terminal contra o servidor MCP."""
    session_manager = os.getenv("SESSION_MANAGER_ID", "mgr_ana")

    async with (
        stdio_client(_server_params()) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools_res = await session.list_tools()
        groq_tools = _build_groq_tools(getattr(tools_res, "tools", []))
        _, system_prompt = await _build_system_prompt(session)

        console.print(
            Panel.fit(
                f"Chat interativo conectado ao YouShop Radar MCP (sessão: {session_manager}).\n"
                "Digite sua pergunta ou 'sair' para encerrar.",
                title="Modo Interativo",
            )
        )

        while True:
            question = console.input("\n[bold cyan]Você:[/bold cyan] ").strip()
            if not question or question.lower() in {"sair", "exit", "quit"}:
                break

            answer, tool_trace = await _run_agent_turn(
                groq_client=groq_client,
                session=session,
                groq_tools=groq_tools,
                system_prompt=system_prompt,
                user_prompt=question,
            )
            for call in tool_trace:
                console.print(f"  [dim]→ MCP Tool: {call['tool']}({call['arguments']})[/dim]")
            console.print(Panel(answer, title="YouShop Radar", border_style="green"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Cliente MCP local com Groq para o YouShop Radar.")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Abre chat interativo no terminal em vez de gravar o roteiro padrão.",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Executa o roteiro de 5 cenas e salva docs/replay_data.json (padrão).",
    )
    args = parser.parse_args()

    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key.startswith("coloque_"):
        console.print(
            "[bold red]Erro:[/bold red] defina uma GROQ_API_KEY válida no arquivo .env local."
        )
        sys.exit(1)

    groq_client = Groq(api_key=api_key)
    if args.interactive:
        asyncio.run(run_interactive_chat(groq_client))
    else:
        asyncio.run(run_recorded_demo(groq_client))


if __name__ == "__main__":
    main()