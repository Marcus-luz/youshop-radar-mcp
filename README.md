# YouShop Radar MCP

[![CI — YouShop Radar MCP](https://github.com/Marcus-luz/youshop-radar-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Marcus-luz/youshop-radar-mcp/actions/workflows/ci.yml)

Protótipo funcional de um **servidor MCP (Model Context Protocol, via SDK Python v2) somente leitura** para apoiar gerentes de contas do Comercial na detecção preventiva de risco de churn de produtores — correlacionando falhas técnicas de webhook, queda de vendas contra baseline e tickets críticos abertos com isolamento de carteira *fail-closed* e minimização de dados via Allowlist.

- **Demo Interativa (Replay Real com Raio-X JSON e Auditoria Numérica):** [https://marcus-luz.github.io/youshop-radar-mcp/](https://marcus-luz.github.io/youshop-radar-mcp/)

---

## Problema e Proposta de Valor

Sinais de risco de um produtor costumam ficar dispersos entre áreas diferentes: falhas de integração (Engenharia/Webhooks), queda de conversão (Vendas) e tickets críticos abertos (Suporte). Quando o Comercial descobre, o produtor já migrou sua operação ou comprometeu o lançamento dos seus creators.

O **YouShop Radar MCP** organiza esse fluxo em três pilares:

1. **Cálculo determinístico fora do LLM:** o score de risco (0 a 100), a distinção entre erro crítico (`alerta_tecnico_precoce`) e erro auxiliar (`falso_alarme_tecnico`) e o tratamento de ausência de baseline são calculados em Python auditável.
2. **IA onde gera valor:** correlacionar sinais de múltiplas áreas, explicar prioridades em linguagem acessível para times não técnicos e redigir rascunhos consultivos de contato — com verificador automático conferindo cada métrica citada contra os payloads MCP.
3. **Segurança e controle humano por padrão:** servidor estritamente somente leitura, sessão *fail-closed*, resposta única anti-enumeração, sanitização de valores contra *prompt injection* e *Human-in-the-Loop* obrigatório (o agente nunca envia mensagens nem altera dados).

---

## Arquitetura em 3 Camadas

```text
youshop-radar-mcp/
├── src/youshop_radar/
│   ├── config.py               # Pesos, limiares, listas fechadas e sessão fail-closed
│   ├── data/                   # Camada de Dados (SQLite readonly + seed=42)
│   │   ├── database.py
│   │   ├── seed.py
│   │   └── repository.py       # Janela semiaberta (>=, <) e tickets abertos de qualquer idade
│   ├── domain/                 # Camada de Negócio e Segurança (Python puro)
│   │   ├── scoring.py          # Score 0–100 + alerta técnico precoce / falso alarme / sem_baseline
│   │   └── security.py         # Isolamento por carteira, anti-enumeração e sanitização Allowlist
│   └── mcp_server/             # Adaptador fino via MCP SDK Python v2 (stdio)
│       ├── playbooks.py        # Resource (guia de exemplo) + Prompt HITL com validação de canal
│       └── server.py           # MCPServer expondo 3 Tools, 1 Resource e 1 Prompt
├── demo_client/
│   └── groq_client.py          # Cliente MCP desacoplado + verificador numérico de respostas
├── docs/
│   ├── index.html              # Interface interativa de replay no GitHub Pages
│   └── replay_data.json        # Gravação real das 5 cenas + payloads MCP + auditoria numérica
└── tests/                      # 15 testes (domínio, bordas de data/ticket, segurança e E2E stdio)
```

---

## Primitivas MCP Expostas

| Tipo | Nome / URI | Descrição |
| :--- | :--- | :--- |
| **Tool** | `list_at_risk_producers(min_score=50)` | Lista produtores da carteira autenticada ordenados por score de risco. |
| **Tool** | `get_producer_signals(producer_ref)` | Detalha sinais dos últimos 7 dias vs. baseline de 21 dias (webhooks, vendas, tickets abertos de qualquer idade, creators). |
| **Tool** | `get_signal_history(producer_ref, weeks=4)` | Evolução semanal (1 a 4 semanas) para identificar o marco inicial da degradação. |
| **Resource** | `youshop://playbooks/retention-guide` | Guia de exemplo de boas práticas de retenção proativa do Comercial. |
| **Prompt** | `draft_retention_outreach(producer_ref, channel)` | Gera rascunho consultivo para `whatsapp`, `email` ou `telefone` com aviso obrigatório de revisão humana. |

---

## Segurança, Privacidade e Uso Consciente da IA

- **Sessão *Fail-Closed* e Anti-Enumeração:** nenhuma tool aceita `manager_id` vindo do modelo. O servidor exige `SESSION_MANAGER_ID` explícito no ambiente (falha se ausente) e devolve o mesmo erro (`PRODUCER_UNAVAILABLE`) tanto para produtores de outra carteira quanto para nomes inexistentes, impedindo enumeração de clientes.
- **Minimização de Dados via Allowlist e Anti-Prompt Injection:** alinhado ao princípio da necessidade da LGPD (sem reivindicar conformidade jurídica plena), apenas métricas agregadas saem do servidor. Colunas sensíveis (`owner_email`, `tax_id`, `webhook_secret`, `buyer_email`) nunca cruzam a fronteira MCP, e valores categóricos (`top_webhook_error`, `tickets_by_category`) são validados contra listas fechadas antes de chegar ao modelo.
- **Distinção entre Erro Crítico e Falso Alarme Técnico:** apenas erros em endpoints auxiliares (`endpoint_analytics_offline`) com vendas estáveis e zero tickets críticos são rebaixados para `falso_alarme_tecnico`. Erros críticos (`assinatura_invalida`, `timeout_checkout_pix`) com vendas ainda estáveis disparam `alerta_tecnico_precoce` para ação preventiva antes da queda de faturamento.
- **Verificador Numérico no Replay:** antes de gravar `docs/replay_data.json`, o cliente audita se as métricas numéricas citadas pelo LLM constam nos payloads devolvidos pelas tools MCP e exibe o resultado na interface web.

---

## Limites e Premissas

Este é um protótipo funcional desenhado para validação técnica e demonstração de arquitetura. Suas limitações declaradas são:

- **Dados simulados e escopo reduzido:** base sintética (`seed=42`) com apenas 8 produtores em 2 carteiras e data de referência fixa (`19/11/2026`).
- **Pesos e limiares hipotéticos:** os pesos (`40%` webhook, `40%` vendas, `20%` tickets) e todos os limiares centralizados em `config.py` são hipóteses iniciais a calibrar com histórico real.
- **Sazonalidade simulada:** em uma janela sintética de 28 dias não há série histórica anual; o padrão sazonal é modelado por uma flag fixa por produtor (`seasonal_pattern`) apenas para demonstrar o comportamento da regra.
- **Identidade simulada por variável de ambiente:** `SESSION_MANAGER_ID` substitui a camada de autenticação neste protótipo; em produção seria vinculada a OAuth2/JWT corporativo e RBAC.
- **Falibilidade da IA:** mesmo com cálculo determinístico, prompt restritivo e verificador numérico, LLMs podem interpretar contexto de forma imprecisa — por isso o fluxo é somente leitura e exige revisão humana antes de qualquer contato.
- **Envio externo para API da Groq:** no cliente de demonstração local, os retornos minimizados das tools MCP são enviados à API da Groq (servidores nos EUA). Antes de operar com dados reais de clientes, seria necessária avaliação jurídica/DPA ou migração para inferência em ambiente controlado.
- **Feedback de "alerta útil" fora do escopo do protótipo:** o mecanismo de marcação de utilidade pelo gerente de contas ainda não foi implementado (o servidor atual é estritamente somente leitura) e está previsto para a fase de piloto.

---

## Piloto e Como Mediríamos o Sucesso

Antes de liberar o Radar para toda a operação comercial, a validação ocorreria em um piloto controlado de 4 a 6 semanas com uma carteira real selecionada, medindo:

| Métrica | Como seria medida | Objetivo no piloto |
| :--- | :--- | :--- |
| **Taxa de "alerta útil" (métrica central)** | Botão de feedback rápido (`útil` / `ruído`) registrado pelo gerente de contas após cada alerta sinalizado. | Validar se os alertas geram ação real (meta inicial: ≥ 70% de alertas úteis) e recalibrar pesos/limiares de `config.py`. |
| **Taxa de falso alarme comercial** | Proporção de alertas de risco médio/alto que não correspondiam a degradação técnica ou queda real. | Reduzir fadiga de alertas no Comercial. |
| **Tempo médio até detecção técnica** | Intervalo entre o início de falhas críticas de webhook/checkout e o primeiro contato ou abertura de suporte. | Antecipar a ação antes que o problema vire churn ou prejuízo em lançamento de creators. |
| **Retenção / recuperação pós-abordagem** | Estabilização de webhooks e retorno da receita ao baseline em 14 dias após contato consultivo. | Medir impacto prático na receita retida. |

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

# 4. Rodar lint e suíte de 15 testes automatizados (inclui E2E via stdio)
uv run ruff check src tests demo_client
uv run pytest -v
```

### Rodar o agente MCP ao vivo (Groq API)

1. Copie `.env.example` para `.env`, defina `SESSION_MANAGER_ID=mgr_ana` e informe sua `GROQ_API_KEY` gratuita ([console.groq.com](https://console.groq.com)).
2. Execute o roteiro completo (ou abra o modo interativo):

```bash
# Regravar a sessão completa de 5 cenas com auditoria numérica (atualiza docs/replay_data.json)
uv run python demo_client/groq_client.py --record

# Conversar livremente no terminal com o servidor MCP via stdio
uv run python demo_client/groq_client.py --interactive

# Visualizar a interface web de replay localmente ([http://127.0.0.1:8000](http://127.0.0.1:8000))
uv run python -m http.server 8000 --bind 127.0.0.1 --directory docs
```

---

## Cenários Demonstrados (Sessão `mgr_ana`)

1. **Priorização da carteira:** foca nos produtores que exigem ação imediata (`Aurora Cosméticos`, score 93; `NutriMax Suplementos`, score 83) sem poluir a resposta com contas estáveis.
2. **Diagnóstico + evolução semanal:** cruza o salto de falhas `assinatura_invalida` (32,1%) com a queda de -40,1% nas vendas na semana de 13–19 nov, usando linguagem de hipótese ("pode indicar").
3. **Rascunho de abordagem (HITL):** dispara o MCP Prompt + Resource e redige mensagem consultiva de WhatsApp sem expor score ou classificação interna ao cliente.
4. **Segurança (carteira alheia):** bloqueia consulta à `BellaSuplementos` (`mgr_carlos`) via `PRODUCER_UNAVAILABLE` (mesmo erro de produtor inexistente) sem sugerir contornos.
5. **Falso alarme técnico:** explica por que `CodeMaster Cursos` (28,6% de erro em `endpoint_analytics_offline`, vendas +7,1% e 0 tickets) não exige ligação urgente de churn.