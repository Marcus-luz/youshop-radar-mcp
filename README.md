# YouShop Radar MCP

[![CI — YouShop Radar MCP](https://github.com/Marcus-luz/youshop-radar-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Marcus-luz/youshop-radar-mcp/actions/workflows/ci.yml)

Protótipo funcional de um **servidor MCP (Model Context Protocol v2) somente leitura** para apoiar gerentes de contas do Comercial na detecção preventiva de risco de churn de produtores — correlacionando falhas técnicas de webhook, queda de vendas contra baseline e tickets críticos de suporte com isolamento de carteira e Allowlist LGPD.

- **Demo Interativa (Replay Real com Raio-X JSON):** [https://marcus-luz.github.io/youshop-radar-mcp/](https://marcus-luz.github.io/youshop-radar-mcp/)

---

## Problema e Proposta de Valor

Sinais de risco de um produtor costumam ficar dispersos entre áreas diferentes: falhas de integração (Engenharia/Webhooks), queda de conversão (Vendas) e tickets críticos (Suporte). Quando o Comercial descobre, o produtor já migrou sua operação ou comprometeu o lançamento dos seus creators.

O **YouShop Radar MCP** resolve essa fragmentação com três diretrizes claras:

1. **Zero matemática na IA:** o score de risco (0 a 100) e as travas de falso alarme técnico / sazonalidade são calculados por regras determinísticas auditáveis em Python.
2. **IA onde ela gera valor:** correlacionar sinais de múltiplas áreas, explicar a prioridade em linguagem simples para times não técnicos e propor rascunhos consultivos de contato.
3. **Segurança e controle humano por padrão:** servidor estritamente somente leitura, isolamento de carteira pela sessão do servidor, privacidade por Allowlist LGPD e *Human-in-the-Loop* obrigatório (o agente nunca envia mensagens nem altera dados).

---

## Arquitetura em 3 Camadas

```text
youshop-radar-mcp/
├── src/youshop_radar/
│   ├── config.py               # Parâmetros determinísticos, pesos e sessão
│   ├── data/                   # Camada de Dados (SQLite readonly + seed=42)
│   │   ├── database.py
│   │   ├── seed.py
│   │   └── repository.py
│   ├── domain/                 # Camada de Negócio e Segurança (Python puro)
│   │   ├── scoring.py          # Score 0–100 + travas de falso alarme e sazonalidade
│   │   └── security.py         # Isolamento por carteira + Allowlist LGPD
│   └── mcp_server/             # Adaptador fino MCP SDK v2 (stdio)
│       ├── playbooks.py        # Resource (guia de retenção) + Prompt HITL
│       └── server.py           # MCPServer expondo 3 Tools, 1 Resource e 1 Prompt
├── demo_client/
│   └── groq_client.py          # Cliente MCP desacoplado (Groq API + gravador de replay)
├── docs/
│   ├── index.html              # Interface interativa de replay no GitHub Pages
│   └── replay_data.json        # Gravação real das 5 cenas + payloads MCP brutos
└── tests/                      # Testes de score, segurança LGPD e contrato MCP
```

---

## Primitivas MCP Expostas

| Tipo | Nome / URI | Descrição |
| :--- | :--- | :--- |
| **Tool** | `list_at_risk_producers(min_score=50)` | Lista produtores da carteira autenticada ordenados por score de risco. |
| **Tool** | `get_producer_signals(producer_ref)` | Detalha sinais dos últimos 7 dias vs. baseline de 21 dias (webhooks, vendas, tickets, creators). |
| **Tool** | `get_signal_history(producer_ref, weeks=4)` | Evolução semanal (1 a 4 semanas) para identificar o marco inicial da degradação. |
| **Resource** | `youshop://playbooks/retention-guide` | Guia oficial de boas práticas de retenção proativa do Comercial. |
| **Prompt** | `draft_retention_outreach(producer_ref, channel)` | Gera rascunho curto de abordagem com aviso obrigatório de revisão e envio humano. |

---

## Segurança, Privacidade e Uso Consciente da IA

- **Isolamento de Carteira na Sessão:** nenhuma tool aceita `manager_id` como parâmetro vindo do modelo. A carteira (`SESSION_MANAGER_ID`) é lida exclusivamente do ambiente do servidor; tentativas de consultar produtores de outra carteira (ex.: `BellaSuplementos` na sessão `mgr_ana`) retornam `ACCESS_DENIED_PORTFOLIO`.
- **Allowlist LGPD:** apenas campos explícitos de métricas agregadas saem do servidor. E-mails de compradores, e-mails de responsáveis, CNPJ (`tax_id`) e segredos de webhook (`webhook_secret`) nunca atravessam a fronteira do MCP — validado por teste automatizado (`tests/test_security.py`).
- **Trava contra Falso Alarme Técnico:** produtores com erro de webhook em endpoint auxiliar sem queda de vendas e sem tickets críticos (ex.: `CodeMaster Cursos`) recebem classificação `falso_alarme_tecnico` e score limitado, evitando acionamento comercial desnecessário.
- **Calibração Explícita:** os pesos atuais (40% webhook, 40% vendas, 20% tickets) são hipóteses iniciais simuladas (`seed=42`) desenhadas para calibração em piloto com histórico real.

---

## Como Rodar Localmente

**Pré-requisitos:** Python 3.11+ e [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Instalar dependências no ambiente virtual isolado
uv sync

# 2. Gerar o banco SQLite simulado determinístico (seed=42)
uv run python -m youshop_radar.data.seed

# 3. Conferir a tabela determinística de scores dos 8 produtores
uv run python -m youshop_radar.domain.scoring

# 4. Rodar lint e suíte de testes automatizados
uv run ruff check src tests demo_client
uv run pytest -v
```

### Rodar o agente MCP ao vivo (Groq API)

1. Copie `.env.example` para `.env` e informe sua `GROQ_API_KEY` gratuita ([console.groq.com](https://console.groq.com)).
2. Execute o roteiro completo (ou abra o modo interativo):

```bash
# Regravar a sessão completa de 5 cenas (atualiza docs/replay_data.json)
uv run python demo_client/groq_client.py --record

# Conversar livremente no terminal com o servidor MCP via stdio
uv run python demo_client/groq_client.py --interactive

# Visualizar a interface web de replay localmente (http://127.0.0.1:8000)
uv run python -m http.server 8000 --bind 127.0.0.1 --directory docs
```

---

## Cenários Demonstrados (Sessão `mgr_ana`)

1. **Priorização da carteira:** identifica `Aurora Cosméticos` (93) e `NutriMax Suplementos` (83) no topo, separando-os de casos estáveis e sazonais.
2. **Diagnóstico + evolução semanal:** cruza o salto de falhas `assinatura_invalida` (32,1%) com a queda de -40,1% nas vendas na semana de 13–19 nov.
3. **Rascunho de abordagem (HITL):** dispara o MCP Prompt + Resource e gera mensagem pronta para revisão humana.
4. **Segurança (carteira alheia):** bloqueia consulta à `BellaSuplementos` (`mgr_carlos`) via `ACCESS_DENIED_PORTFOLIO`.
5. **Falso alarme técnico:** explica por que `CodeMaster Cursos` (28,6% de erro em analytics, mas vendas +7,1% e 0 tickets) não exige ligação urgente de churn.