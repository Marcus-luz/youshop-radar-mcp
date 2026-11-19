"""Playbook de retenção (MCP Resource) e template de abordagem (MCP Prompt)."""

from __future__ import annotations

RETENTION_PLAYBOOK_URI = "youshop://playbooks/retention-guide"

RETENTION_PLAYBOOK_TEXT = """# Guia de Boas Práticas de Retenção — Comercial YouShop

## Princípios de Abordagem Proativa
1. **Apoio antes de cobrança:** O Radar existe para ajudar o produtor a vender melhor e proteger
   o lançamento e a comissão dos seus creators — nunca para pressionar ou culpar o cliente.
2. **Transparência com humildade:** Cite apenas os sinais medidos (ex.: taxa de falha de webhook,
   variação de vendas e tickets abertos). Se houver erro técnico sem queda de vendas (falso alarme
   técnico), trate como aviso preventivo de baixa urgência e admita explicitamente o que os dados
   não comprovam.
3. **Ação concreta na primeira mensagem:** Ofereça um passo prático imediato (ex.: revisar segredo
   do webhook ou acionar suporte técnico da YouShop em chamada rápida de 10 minutos).
4. **Tom de voz:** Direto, consultivo, empático e livre de jargão técnico excessivo.
5. **Pessoa no controle (Human-in-the-Loop):** Todo texto gerado é um rascunho para revisão e
   edição do gerente de contas. O agente nunca envia mensagens diretamente.
"""


def build_outreach_prompt(producer_ref: str, channel: str = "whatsapp") -> str:
    """Monta a instrução estruturada do MCP Prompt para redigir rascunho de contato."""
    safe_channel = channel.strip().lower() or "whatsapp"
    return (
        f"Você é o assistente YouShop Radar apoiando um gerente de contas do Comercial.\n"
        f"1. Consulte os sinais atuais do produtor '{producer_ref}' usando `get_producer_signals` "
        f"e, se necessário, o histórico em `get_signal_history`.\n"
        f"2. Siga rigorosamente o guia em `{RETENTION_PLAYBOOK_URI}`.\n"
        f"3. Se o acesso for negado (carteira de outro gerente), informe a recusa de segurança "
        f"e NÃO invente dados.\n"
        f"4. Se o nível for `falso_alarme_tecnico` ou `baixo_sazonalidade`, explique claramente "
        f"por que não há evidência de risco comercial imediato antes de sugerir qualquer contato.\n"
        f"5. Redija um rascunho curto para o canal '{safe_channel}' (máximo 5 linhas), "
        f"citando os sinais medidos de forma simples e oferecendo apoio técnico.\n"
        f"6. Comece obrigatoriamente o rascunho com a linha: "
        f"'[RASCUNHO — REVISÃO E ENVIO HUMANO OBRIGATÓRIOS]'."
    )