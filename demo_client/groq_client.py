"""Cliente MCP local conectado via stdio à API do Groq.

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
import re
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
            "seguindo o guia de retenção (sem enviar)."
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

_MONTHS_RE = r"(?:jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\.?"
_DATE_RANGE_RE = re.compile(
    rf"\b\d{{1,2}}\s*(?:[–-]|a)\s*\d{{1,2}}\s*(?:de\s+)?{_MONTHS_RE}(?:\s*(?:de\s+)?\d{{2,4}})?\b",
    re.IGNORECASE,
)
_SINGLE_DATE_RE = re.compile(
    rf"\b\d{{1,2}}\s+(?:de\s+)?{_MONTHS_RE}(?:\s+(?:de\s+)?\d{{2,4}})?\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_YEAR_RE = re.compile(r"\b20\d{2}\b")
_WINDOW_OR_TIME_RE = re.compile(
    r"\b(?:7|21)\s*dias?\b|\b[1-4]\s*semanas?\b|\b\d+\s*(?:min(?:utos?)?|h(?:oras?)?)\b",
    re.IGNORECASE,
)
_ORDINAL_RE = re.compile(r"(?m)^\s*\d+\.\s+|\bprioridade\s+\d+\b", re.IGNORECASE)

_PCT_RE = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*%")
_BRL_RE = re.compile(r"R\$\s*([\d.\s\u00a0\u202f]+,\d{2}|\d+(?:[.,]\d+)?)")
_METRIC_AFTER_RE = re.compile(
    r"(?:score(?:\s+de\s+risco)?|pedidos?\s+recentes?|tickets?\s+abertos?|"
    r"tickets?\s+cr[ií]ticos?|criadores?\s+ativos?|creators?\s+ativos?)"
    r"[^\d\n]{0,12}(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_METRIC_BEFORE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:pedidos?|eventos?|falhas?|tickets?(?:\s+cr[ií]ticos?)?|"
    r"creators?|criadores?)\b",
    re.IGNORECASE,
)


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

_SKIP_GROUNDING_KEYS = frozenset({"calibration_note", "data_notice", "week_range"})


def _collect_payload_numbers(obj: Any, out: set[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.add(abs(float(obj)))
        return
    if isinstance(obj, str):
        for match in re.findall(r"\d+(?:\.\d+)?", obj):
            out.add(abs(float(match)))
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in _SKIP_GROUNDING_KEYS:
                continue
            _collect_payload_numbers(val, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_payload_numbers(item, out)


def _parse_ptbr_number(raw: str) -> float | None:
    cleaned = re.sub(r"[\s\u00a0\u202f]+", "", raw).lstrip("+-")
    if not cleaned:
        return None
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return abs(float(cleaned))
    except ValueError:
        return None


def _extract_table_numbers(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or re.match(r"^\|[\s:|-]+\|$", stripped):
            continue
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        for cell in cells[1:]:
            plain = re.sub(r"[*_`~]", "", cell).strip()
            if re.fullmatch(r"[+-]?[\d.\s\u00a0\u202f]+(?:,\d+)?%?", plain):
                found.append(plain.rstrip("%").strip())
    return found


def verify_grounded_numbers(answer: str, tool_trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Confere se os números de métricas citados na resposta existem nos payloads MCP."""
    grounded_pool: set[float] = set()
    for call in tool_trace:
        _collect_payload_numbers(call.get("result"), grounded_pool)

    # Normaliza sinais especiais (ex.: hífen não separável U+2011)
    normalized = answer.replace("\u2011", "-").replace("\u2212", "-")
    scrubbed = _ISO_DATE_RE.sub(" ", normalized)
    scrubbed = _DATE_RANGE_RE.sub(" ", scrubbed)
    scrubbed = _SINGLE_DATE_RE.sub(" ", scrubbed)
    scrubbed = _YEAR_RE.sub(" ", scrubbed)
    scrubbed = _WINDOW_OR_TIME_RE.sub(" ", scrubbed)
    scrubbed = _ORDINAL_RE.sub(" ", scrubbed)

    raw_candidates: list[str] = []
    for pattern in (_PCT_RE, _BRL_RE, _METRIC_AFTER_RE, _METRIC_BEFORE_RE):
        raw_candidates.extend(pattern.findall(scrubbed))
    raw_candidates.extend(_extract_table_numbers(scrubbed))

    checked: list[str] = []
    ungrounded: list[str] = []
    seen_values: set[float] = set()

    for raw in raw_candidates:
        val = _parse_ptbr_number(raw)
        if val is None or val in seen_values:
            continue
        seen_values.add(val)
        label = raw.strip()
        checked.append(label)
        if not any(abs(val - ref) <= 0.05 for ref in grounded_pool):
            ungrounded.append(label)

    return {
        "verified": len(ungrounded) == 0,
        "checked_count": len(checked),
        "checked_numbers": checked,
        "ungrounded_numbers": ungrounded,
    }


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
            temperature=0.0,
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
        "para apoiar gerentes de contas do Comercial.\n"
        "Regras obrigatórias:\n"
        "1. Sempre consulte as ferramentas MCP antes de responder. Nunca invente números nem "
        "crie exemplos fictícios de tickets (como 'falha de pagamento', 'checkout travado' ou "
        "'reembolso automático'). Copie exatamente as casas decimais retornadas pelo servidor "
        "(ex.: escreva '32,1%' e '-40,1%', NUNCA arredonde para '32%' ou '40%').\n"
        "2. Ao responder sobre quem precisa de atenção hoje, chame `list_at_risk_producers` com o "
        "padrão `min_score=50` para focar em quem realmente exige ação. NÃO liste em detalhes "
        "produtores saudáveis com score baixo/zero; informe apenas o total de contas sinalizadas "
        "frente ao total da carteira.\n"
        "3. Ao explicar causas técnicas prováveis (ex.: erro `assinatura_invalida`), use sempre "
        "linguagem de hipótese ('pode indicar'), nunca como certeza absoluta. Use também o nome "
        "exato do `risk_level` retornado (não chame um produtor de nível `alto` de "
        "'alerta técnico precoce').\n"
        "4. Em rascunhos de contato ao produtor (WhatsApp/e-mail/telefone), JAMAIS mencione score "
        "numérico, 'baseline', classificação interna ('risco comercial alto') ou a palavra "
        "'churn'. Use os percentuais exatos com 1 casa decimal (ex.: '32,1%' e '40,1%'), adote "
        "tom consultivo e parceiro e comece com "
        "'[RASCUNHO — REVISÃO E ENVIO HUMANO OBRIGATÓRIOS]'.\n"
        "5. Se uma ferramenta retornar `PRODUCER_UNAVAILABLE`, responda de forma curta e objetiva "
        "(no máximo 2 frases) informando apenas que o produtor não está disponível na carteira "
        "autenticada e que eventuais dúvidas de escopo devem ser tratadas com a gestão comercial. "
        "É PROIBIDO sugerir contornos como pedir relatórios ao produtor ou abrir ticket para "
        "'acesso temporário'.\n"
        "6. Se o nível for `falso_alarme_tecnico`, destaque explicitamente o que os dados NÃO "
        "comprovam (erro em endpoint auxiliar sem queda de vendas e sem tickets críticos) e "
        "recomende não tratar como churn urgente.\n"
        "7. Responda sempre em português do Brasil, de forma objetiva e clara para não técnicos.\n\n"
        f"Guia de exemplo carregado do MCP Resource ({playbook_uri}):\n{playbook_text}"
    )
    return playbook_uri, system_prompt


def _server_params(session_manager_id: str) -> StdioServerParameters:
    env = dict(os.environ)
    env["SESSION_MANAGER_ID"] = session_manager_id
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "youshop_radar.mcp_server.server"],
        env=env,
    )


async def run_recorded_demo(groq_client: Groq, session_manager: str) -> None:
    """Executa o roteiro de 5 cenas, audita números e grava docs/replay_data.json."""
    async with (
        stdio_client(_server_params(session_manager)) as (read_stream, write_stream),
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

            grounding = verify_grounded_numbers(answer, tool_trace)

            for call in tool_trace:
                args_str = json.dumps(call["arguments"], ensure_ascii=False)
                console.print(f"  [dim]→ MCP Tool: {call['tool']}({args_str})[/dim]")

            if grounding["verified"]:
                console.print(
                    f"  [green]✓ Verificador numérico: {grounding['checked_count']} "
                    "métrica(s) conferida(s) nos payloads MCP[/green]"
                )
            else:
                console.print(
                    f"  [bold yellow]⚠ Verificador numérico encontrou valores fora dos payloads: "
                    f"{grounding['ungrounded_numbers']}[/bold yellow]"
                )

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
                    "grounding_check": grounding,
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
                    "Gravação de sessão real do servidor MCP com dados simulados (seed=42, "
                    "data de referência 19/11/2026). Nenhuma ação real é executada."
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


async def run_interactive_chat(groq_client: Groq, session_manager: str) -> None:
    """Abre um chat interativo ao vivo no terminal contra o servidor MCP."""
    async with (
        stdio_client(_server_params(session_manager)) as (read_stream, write_stream),
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

    session_manager = os.getenv("SESSION_MANAGER_ID", "").strip()
    if not session_manager:
        console.print(
            "[bold red]Erro:[/bold red] defina SESSION_MANAGER_ID (ex.: mgr_ana) no arquivo .env."
        )
        sys.exit(1)

    groq_client = Groq(api_key=api_key)
    if args.interactive:
        asyncio.run(run_interactive_chat(groq_client, session_manager))
    else:
        asyncio.run(run_recorded_demo(groq_client, session_manager))


if __name__ == "__main__":
    main()