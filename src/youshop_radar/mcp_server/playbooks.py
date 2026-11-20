"""Guia de exemplo de retenção (MCP Resource) e template de abordagem (MCP Prompt)."""

from __future__ import annotations

from youshop_radar.config import ALLOWED_OUTREACH_CHANNELS
from youshop_radar.domain.security import sanitize_producer_ref

RETENTION_PLAYBOOK_URI = "youshop://playbooks/retention-guide"

RETENTION_PLAYBOOK_TEXT = """# Guia de Exemplo de Retenção Proativa — Comercial (Protótipo)

## Princípios de Abordagem Proativa
1. **Apoio antes de cobrança:** O contato existe para ajudar o produtor a vender melhor e proteger
   o lançamento e a comissão dos seus creators — nunca para pressionar ou expor alertas internos.
2. **Nunca expor classificação interna nem jargão técnico ao cliente:** Em rascunhos enviados ao
   produtor, JAMAIS cite score numérico, "risco comercial alto", "baseline" ou a palavra "churn".
   Prefira termos simples como "média das últimas 3 semanas" e fale como parceiro oferecendo ajuda.
3. **Transparência com humildade:** Cite apenas os sinais medidos retornados pelas ferramentas.
   Use sempre linguagem de hipótese ("pode indicar") ao apontar causas técnicas prováveis. Nunca
   invente categorias ou exemplos de tickets ausentes dos dados.
4. **Distinção de gravidade técnica:**
   - `falso_alarme_tecnico` (apenas endpoint auxiliar com vendas estáveis): baixa urgência; deixe
     explícito que os dados não comprovam risco comercial.
   - `alerta_tecnico_precoce` (erro crítico ou desconhecido com vendas ainda estáveis): atenção
     preventiva técnica para evitar queda de vendas.
5. **Ação concreta na primeira mensagem:** Ofereça um passo prático imediato (ex.: revisar a
   configuração do webhook junto com o suporte técnico em uma chamada rápida de 10 minutos).
6. **Pessoa no controle (Human-in-the-Loop):** Todo texto gerado é um rascunho para revisão e
   edição do gerente de contas. O agente nunca envia mensagens diretamente.
"""


def build_outreach_prompt(producer_ref: str, channel: str = "whatsapp") -> str:
    """Monta a instrução estruturada do MCP Prompt para redigir rascunho de contato."""
    safe_ref = sanitize_producer_ref(producer_ref)
    safe_channel = channel.strip().lower()
    if safe_channel not in ALLOWED_OUTREACH_CHANNELS:
        allowed_str = ", ".join(sorted(ALLOWED_OUTREACH_CHANNELS))
        raise ValueError(f"Canal inválido: '{channel}'. Canais permitidos: {allowed_str}.")

    return (
        f"Você é o assistente YouShop Radar apoiando um gerente de contas do Comercial.\n"
        f"1. Consulte os sinais atuais do produtor '{safe_ref}' usando `get_producer_signals`.\n"
        f"2. Siga rigorosamente o guia de exemplo em `{RETENTION_PLAYBOOK_URI}`.\n"
        f"3. Se o servidor retornar `PRODUCER_UNAVAILABLE`, informe apenas que o produtor não está "
        f"disponível na carteira autenticada, indique tratar com a gestão e NÃO invente dados nem "
        f"processos de contorno.\n"
        f"4. Se o nível for `falso_alarme_tecnico` ou `baixo_sazonalidade`, explique claramente "
        f"por que não há evidência de risco comercial imediato antes de sugerir qualquer contato.\n"
        f"5. Redija um rascunho curto, natural e consultivo para o canal '{safe_channel}' "
        f"(máximo 5 linhas), usando português correto ('nos últimos 7 dias', 'média dos 21 dias "
        f"anteriores'), citando os percentuais exatos e oferecendo apoio técnico prático em uma "
        f"chamada rápida de 10 minutos. JAMAIS mencione score, 'baseline', 'risco comercial alto' "
        f"ou 'churn' no texto destinado ao produtor, e use 'pode indicar' para causas prováveis.\n"
        f"6. Comece obrigatoriamente o rascunho com a linha: "
        f"'[RASCUNHO — REVISÃO E ENVIO HUMANO OBRIGATÓRIOS]'."
    )