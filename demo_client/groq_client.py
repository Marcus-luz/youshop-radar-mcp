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
from groq import Groq, RateLimitError
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
        "badge": "Visão Comercial + Alerta Precoce",
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

_GLOBAL_FORBIDDEN_PHRASES = (
    "falha de pagamento",
    "checkout travado",
    "reembolso automático",
    "reembolso automatico",
)

_SCENE_POLICY_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "priorizacao": {
        "required": ("aurora cosméticos", "nutrimax suplementos", "vitapulse saúde"),
        "forbidden": (),
    },
    "diagnostico_aurora": {
        "required": (),
        "required_any": ("pode indicar", "podem indicar", "pode estar", "podem estar"),
        "forbidden": (),
    },
    "rascunho_contato": {
        "required": ("[rascunho — revisão e envio humano obrigatórios]",),
        "forbidden": (
            "baseline",
            "score",
            "churn",
            "risco comercial alto",
            "últimas 7 dias",
            "ultimas 7 dias",
            "assinatura_invalida",
            "timeout_checkout_pix",
            "endpoint_analytics_offline",
            "erro_nao_catalogado",
        ),
    },
    "recusa_carteira": {
        "required": (),
        "forbidden": (
            "acesso temporário",
            "acesso temporario",
            "abrir ticket",
            "pedir ao produtor",
            "relatórios ao produtor",
            "relatorios ao produtor",
        ),
    },
    "falso_alarme": {
        "required": (),
        "required_any": (
            "falso_alarme_tecnico",
            "falso alarme técnico",
            "falso alarme tecnico",
        ),
        "forbidden": (),
    },
}

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
_ORDINAL_RE = re.compile(r"(?m)^\s*\d+\.\s+|\bprioridade\s+\d+\b", re.IGNORECASE)

_PCT_RE = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*%")
_BRL_RE = re.compile(r"R\$\s*([\d.\s\u00a0\u202f]+,\d{2}|\d+(?:[.,]\d+)?)")
_SCORE_RE = re.compile(r"score(?:\s+de\s+risco)?[^\d\n]{0,18}(\d+(?:[.,]\d+)?)", re.IGNORECASE)
_COUNT_AFTER_RE = re.compile(
    r"(?:pedidos?\s+recentes?|tickets?\s+abertos?|tickets?\s+cr[ií]ticos?|"
    r"criadores?\s+ativos?|creators?\s+ativos?|produtores?\s+na\s+carteira|"
    r"produtores?\s+sinalizados?)[^\d\n]{0,14}(\d+)"
    r"(?!\s*(?:d\b|dias?|semanas?|meses?|h(?:oras?)?|min(?:utos?)?|%))",
    re.IGNORECASE,
)
_COUNT_BEFORE_RE = re.compile(
    r"(\d+)\s*(?:pedidos?|eventos?|falhas?|tickets?(?:\s+cr[ií]ticos?)?|"
    r"creators?|criadores?|produtores?)\b",
    re.IGNORECASE,
)
_TIME_DAYS_RE = re.compile(r"(\d+)\s*dias?\b", re.IGNORECASE)
_TIME_WEEKS_RE = re.compile(r"(\d+)\s*semanas?\b", re.IGNORECASE)
_TIME_MINUTES_RE = re.compile(r"(\d+)\s*min(?:utos?)?\b", re.IGNORECASE)
_TIME_HOURS_RE = re.compile(r"(\d+)\s*h(?:oras?)?\b", re.IGNORECASE)

_PCT_KEYS = frozenset({"webhook_failure_rate_pct", "sales_variation_vs_baseline_pct"})
_SCORE_KEYS = frozenset({"risk_score", "min_score_filter"})
_BRL_KEYS = frozenset({"revenue_brl"})
_COUNT_KEYS = frozenset(
    {
        "orders_count",
        "recent_orders_count",
        "webhook_failed_events",
        "webhook_total_events",
        "open_tickets_total",
        "open_critical_tickets",
        "tickets_opened",
        "active_creators_count",
        "total_portfolio_producers",
        "flagged_count",
    }
)
_COUNT_MAP_KEYS = frozenset({"tickets_by_category", "webhook_errors_by_category"})
_DAY_KEYS = frozenset({"recent_window_days", "baseline_window_days"})
_WEEK_KEYS = frozenset({"weeks_analyzed"})

_GENERIC_VALUE_HEADERS = frozenset(
    {"valor", "valores", "dado", "dados", "resultado", "atual", "medida"}
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


def _empty_typed_pools() -> dict[str, set[float]]:
    return {
        "pct": set(),
        "score": set(),
        "brl": set(),
        "count": set(),
        "time_days": set(),
        "time_weeks": set(),
        "time_minutes": set(),
        "time_hours": set(),
    }


def _populate_typed_pools(obj: Any, pools: dict[str, set[float]], parent_key: str = "") -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        val = abs(float(obj))
        if parent_key in _PCT_KEYS:
            pools["pct"].add(val)
        elif parent_key in _SCORE_KEYS:
            pools["score"].add(val)
        elif parent_key in _BRL_KEYS:
            pools["brl"].add(val)
        elif parent_key in _COUNT_KEYS:
            pools["count"].add(val)
        elif parent_key in _DAY_KEYS:
            pools["time_days"].add(val)
            if val % 7 == 0 and val > 0:
                pools["time_weeks"].add(val / 7.0)
        elif parent_key in _WEEK_KEYS:
            pools["time_weeks"].add(val)
        return

    if isinstance(obj, str) and parent_key == "main_signal":
        for raw_pct in re.findall(r"([+-]?\d+(?:\.\d+)?)\s*%", obj):
            pools["pct"].add(abs(float(raw_pct)))
        for raw_days in re.findall(r"(\d+)\s*(?:d\b|dias?\b)", obj):
            d_val = float(raw_days)
            pools["time_days"].add(d_val)
            if d_val % 7 == 0 and d_val > 0:
                pools["time_weeks"].add(d_val / 7.0)
        for raw_cnt in re.findall(r"(\d+)\s*ticket", obj):
            pools["count"].add(float(raw_cnt))
        return

    if isinstance(obj, dict):
        if parent_key in _COUNT_MAP_KEYS:
            for val in obj.values():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    pools["count"].add(abs(float(val)))
            return
        for key, val in obj.items():
            _populate_typed_pools(val, pools, parent_key=key)
    elif isinstance(obj, list):
        for item in obj:
            _populate_typed_pools(item, pools, parent_key=parent_key)


def _populate_playbook_times(playbook_text: str, pools: dict[str, set[float]]) -> None:
    if not playbook_text:
        return
    for raw in _TIME_MINUTES_RE.findall(playbook_text):
        pools["time_minutes"].add(float(raw))
    for raw in _TIME_HOURS_RE.findall(playbook_text):
        pools["time_hours"].add(float(raw))
    for raw in _TIME_WEEKS_RE.findall(playbook_text):
        pools["time_weeks"].add(float(raw))
    for raw in _TIME_DAYS_RE.findall(playbook_text):
        pools["time_days"].add(float(raw))


def _classify_table_label(label: str) -> str:
    h = label.lower()
    if "%" in h or "taxa" in h or "varia" in h:
        return "pct"
    if "r$" in h or "brl" in h or "receita" in h:
        return "brl"
    if "score" in h:
        return "score"
    if any(
        k in h for k in ("pedido", "order", "evento", "ticket", "criador", "creator", "produtor")
    ):
        return "count"
    if any(k in h for k in ("dia", "day", "janela", "window")):
        return "time_days"
    if any(k in h for k in ("semana", "week")):
        return "time_weeks"
    return "count"


def _extract_typed_table_claims(text: str) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if (
            line.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip())
        ):
            headers = [c.strip() for c in line.split("|")[1:-1]]
            col_types = [_classify_table_label(h) for h in headers]
            is_kv_table = len(headers) == 2 and headers[1].lower() in _GENERIC_VALUE_HEADERS
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().split("|")[1:-1]]
                row_label = cells[0] if cells else ""
                for idx, cell in enumerate(cells[1:], start=1):
                    if idx >= len(col_types):
                        continue
                    plain = re.sub(r"[*_`~]", "", cell).strip()
                    if re.fullmatch(r"[+-]?[\d.\s\u00a0\u202f]+(?:,\d+)?\s*%?", plain):
                        if plain.endswith("%"):
                            col_type = "pct"
                        elif is_kv_table:
                            col_type = _classify_table_label(row_label)
                        else:
                            col_type = col_types[idx]
                        claims.append((col_type, plain.rstrip("%").strip()))
                i += 1
            continue
        i += 1
    return claims


def verify_grounded_numbers(
    answer: str,
    tool_trace: list[dict[str, Any]],
    playbook_text: str = "",
) -> dict[str, Any]:
    """Audita números por categoria tipada contra os payloads MCP e o playbook."""
    pools = _empty_typed_pools()
    for call in tool_trace:
        _populate_typed_pools(call.get("result"), pools)
    _populate_playbook_times(playbook_text, pools)

    normalized = answer.replace("\u2011", "-").replace("\u2212", "-")
    scrubbed = _ISO_DATE_RE.sub(" ", normalized)
    scrubbed = _DATE_RANGE_RE.sub(" ", scrubbed)
    scrubbed = _SINGLE_DATE_RE.sub(" ", scrubbed)
    scrubbed = _YEAR_RE.sub(" ", scrubbed)
    scrubbed = _ORDINAL_RE.sub(" ", scrubbed)

    claims: list[tuple[str, str]] = []
    for raw in _PCT_RE.findall(scrubbed):
        claims.append(("pct", raw))
    for raw in _BRL_RE.findall(scrubbed):
        claims.append(("brl", raw))
    for raw in _SCORE_RE.findall(scrubbed):
        claims.append(("score", raw))
    for raw in _COUNT_AFTER_RE.findall(scrubbed):
        claims.append(("count", raw))
    for raw in _COUNT_BEFORE_RE.findall(scrubbed):
        claims.append(("count", raw))
    for raw in _TIME_DAYS_RE.findall(scrubbed):
        claims.append(("time_days", f"{raw}d"))
    for raw in _TIME_WEEKS_RE.findall(scrubbed):
        claims.append(("time_weeks", f"{raw}sem"))
    for raw in _TIME_MINUTES_RE.findall(scrubbed):
        claims.append(("time_minutes", f"{raw}min"))
    for raw in _TIME_HOURS_RE.findall(scrubbed):
        claims.append(("time_hours", f"{raw}h"))
    claims.extend(_extract_typed_table_claims(scrubbed))

    checked: list[str] = []
    ungrounded: list[str] = []
    seen: set[tuple[str, float]] = set()

    for category, raw_label in claims:
        numeric_part = re.sub(r"[a-zA-Z]+$", "", raw_label).strip()
        val = _parse_ptbr_number(numeric_part)
        if val is None or (category, val) in seen:
            continue
        seen.add((category, val))
        tagged = f"{category}:{raw_label.strip()}"
        checked.append(tagged)
        allowed_pool = pools.get(category, set())
        if not any(abs(val - ref) <= 0.05 for ref in allowed_pool):
            ungrounded.append(tagged)

    return {
        "verified": len(ungrounded) == 0,
        "typed_audit": True,
        "checked_count": len(checked),
        "checked_numbers": checked,
        "ungrounded_numbers": ungrounded,
    }


def verify_scene_policies(scene_id: str, answer: str) -> dict[str, Any]:
    """Valida regras editoriais e de segurança por cena (frases proibidas e obrigatórias)."""
    lowered = answer.lower()
    violations: list[str] = []

    for phrase in _GLOBAL_FORBIDDEN_PHRASES:
        if phrase in lowered:
            violations.append(f"termo_inventado_proibido:{phrase}")

    rules = _SCENE_POLICY_RULES.get(scene_id, {})
    for forbidden in rules.get("forbidden", ()):
        if forbidden in lowered:
            violations.append(f"proibido_na_cena:{forbidden}")

    for required in rules.get("required", ()):
        if required not in lowered:
            violations.append(f"ausente_na_cena:{required}")

    required_any = rules.get("required_any", ())
    if required_any and not any(opt in lowered for opt in required_any):
        violations.append(f"ausente_na_cena:{'|'.join(required_any)}")

    if scene_id == "rascunho_contato" and re.search(r"\b\d+\.\d+\s*%", answer):
        violations.append("proibido_na_cena:ponto_decimal_ingles")

    return {
        "passed": len(violations) == 0,
        "violations": violations,
    }


def audit_recorded_step(
    step: dict[str, Any], playbook_text: str = ""
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Executa auditoria numérica tipada + política editorial sobre uma cena gravada."""
    answer = step.get("assistant_response", "")
    tool_trace = step.get("mcp_tool_calls", [])
    grounding = verify_grounded_numbers(answer, tool_trace, playbook_text=playbook_text)
    policy = verify_scene_policies(step.get("id", ""), answer)
    return grounding, policy


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
        response = None
        for attempt in range(4):
            try:
                response = groq_client.chat.completions.create(
                    model=MODEL_NAME,
                    messages=messages,
                    tools=groq_tools,
                    tool_choice="auto",
                    temperature=0.0,
                )
                break
            except RateLimitError:
                if attempt == 3:
                    raise
                wait_s = 3.0 * (attempt + 1)
                console.print(
                    f"  [dim]⏳ Limite TPM da Groq atingido; aguardando {wait_s:.0f}s...[/dim]"
                )
                await asyncio.sleep(wait_s)
        assert response is not None
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


async def _build_system_prompt(session: ClientSession) -> tuple[str, str, str]:
    playbook_uri, playbook_text = await _load_playbook_resource(session)
    system_prompt = (
        "Você é o YouShop Radar, um agente analítico somente leitura conectado via MCP "
        "para apoiar gerentes de contas do Comercial.\n"
        "Regras obrigatórias:\n"
        "1. Sempre consulte as ferramentas MCP antes de responder. Cite exclusivamente os valores "
        "numéricos, prazos e categorias exatos retornados pelas ferramentas ou pelo guia. "
        "Mesmo ao explicar em linguagem simples, JAMAIS arredonde, faça aproximações ou crie "
        "analogias numéricas (não escreva 'mais de 30%', 'cerca de 23%', 'em cada 10 eventos' ou "
        "'de 0 a 100%'): copie apenas os números literais das ferramentas com todas as casas "
        "decimais. Não acrescente exemplos extras de tickets e não invente prazos em horas.\n"
        "2. Ao responder sobre quem precisa de atenção hoje, chame `list_at_risk_producers` com o "
        "padrão `min_score=50` e apresente todos os produtores sinalizados em ordem de prioridade "
        "(incluindo alertas técnicos precoces), informando o total sinalizado frente ao total da "
        "carteira.\n"
        "3. Sempre que detalhar sinais de um produtor com erro de webhook, inclua uma "
        "hipótese curta de causa provável usando 'pode indicar' ou 'podem indicar' (nunca "
        "certeza absoluta) e preserve o nome exato do `risk_level` retornado.\n"
        "4. Em rascunhos de contato ao produtor, JAMAIS mencione score numérico, 'baseline', "
        "classificação interna, códigos com sublinhado (como `assinatura_invalida`) ou a palavra "
        "'churn'. Use português correto ('nos últimos 7 dias', 'em relação à média dos 21 dias "
        "anteriores'), decimais com vírgula (ex.: 32,1% e 40,1%), adote tom consultivo e comece "
        "com '[RASCUNHO — REVISÃO E ENVIO HUMANO OBRIGATÓRIOS]'.\n"
        "5. Se uma ferramenta retornar `PRODUCER_UNAVAILABLE`, responda em no máximo 2 frases "
        "informando apenas que o produtor não está disponível na carteira autenticada e que "
        "dúvidas de escopo devem ser tratadas com a gestão comercial. Nunca sugira contornos.\n"
        "6. Se o nível for `falso_alarme_tecnico`, destaque explicitamente o que os dados NÃO "
        "comprovam (erro apenas em endpoint auxiliar sem queda de vendas e sem tickets críticos) "
        "e oriente não tratar como urgência de churn.\n"
        "7. Responda sempre em português do Brasil, de forma clara para não técnicos.\n\n"
        f"Guia de exemplo carregado do MCP Resource ({playbook_uri}):\n{playbook_text}"
    )
    return playbook_uri, playbook_text, system_prompt


def _server_params(session_manager_id: str) -> StdioServerParameters:
    env = dict(os.environ)
    env["SESSION_MANAGER_ID"] = session_manager_id
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "youshop_radar.mcp_server.server"],
        env=env,
    )


async def run_recorded_demo(groq_client: Groq, session_manager: str) -> bool:
    """Executa o roteiro de 5 cenas, audita rigorosamente e bloqueia gravação se falhar."""
    async with (
        stdio_client(_server_params(session_manager)) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()

        tools_res = await session.list_tools()
        mcp_tools = getattr(tools_res, "tools", [])
        prompts_res = await session.list_prompts()
        mcp_prompts = getattr(prompts_res, "prompts", [])

        playbook_uri, playbook_text, system_prompt = await _build_system_prompt(session)
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
        audit_failures: list[str] = []

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

            step_obj: dict[str, Any] = {
                "id": scenario["id"],
                "title": scenario["title"],
                "badge": scenario["badge"],
                "user_message": scenario["user_message"],
                "mcp_prompt_used": mcp_prompt_used,
                "mcp_tool_calls": tool_trace,
                "assistant_response": answer,
            }
            grounding, policy = audit_recorded_step(step_obj, playbook_text=playbook_text)
            step_obj["grounding_check"] = grounding
            step_obj["policy_check"] = policy

            for call in tool_trace:
                args_str = json.dumps(call["arguments"], ensure_ascii=False)
                console.print(f"  [dim]→ MCP Tool: {call['tool']}({args_str})[/dim]")

            if grounding["verified"] and policy["passed"]:
                console.print(
                    f"  [green]✓ Auditoria tipada + políticas: {grounding['checked_count']} "
                    "métrica(s) conferida(s) por tipo[/green]"
                )
            else:
                if not grounding["verified"]:
                    msg = f"{scenario['id']} (numérico): {grounding['ungrounded_numbers']}"
                    audit_failures.append(msg)
                    console.print(f"  [bold red]✗ Falha numérica tipada: {msg}[/bold red]")
                if not policy["passed"]:
                    msg = f"{scenario['id']} (política): {policy['violations']}"
                    audit_failures.append(msg)
                    console.print(f"  [bold red]✗ Falha de política: {msg}[/bold red]")

            console.print(
                Panel(answer, title=f"YouShop Radar ({MODEL_NAME} + MCP)", border_style="green")
            )

            recorded_steps.append(step_obj)
            await asyncio.sleep(2.0)

        if audit_failures:
            console.print(
                "\n[bold red]GRAVAÇÃO BLOQUEADA:[/bold red] o replay falhou na auditoria "
                "e docs/replay_data.json NÃO foi sobrescrito."
            )
            for failure in audit_failures:
                console.print(f"  - {failure}")
            return False

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
            f"\n[bold green]Sessão auditada e gravada com sucesso em:[/bold green] "
            f"{REPLAY_OUTPUT_PATH}"
        )
        return True


async def run_interactive_chat(groq_client: Groq, session_manager: str) -> None:
    """Abre um chat interativo ao vivo no terminal contra o servidor MCP."""
    async with (
        stdio_client(_server_params(session_manager)) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools_res = await session.list_tools()
        groq_tools = _build_groq_tools(getattr(tools_res, "tools", []))
        _, _, system_prompt = await _build_system_prompt(session)

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
        help="Executa o roteiro de 5 cenas, audita e salva docs/replay_data.json (padrão).",
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
        ok = asyncio.run(run_recorded_demo(groq_client, session_manager))
        if not ok:
            sys.exit(1)


if __name__ == "__main__":
    main()