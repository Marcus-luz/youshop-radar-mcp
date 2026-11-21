/**
 * Espelho determinístico da função evaluate_risk() de src/youshop_radar/domain/scoring.py.
 * Validado automaticamente contra 14 casos gerados pelo Python em tests/test_web_parity.py.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RadarScoring = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function pythonRound(value) {
    const floor = Math.floor(value);
    const diff = value - floor;
    if (Math.abs(diff - 0.5) < 1e-9) {
      return floor % 2 === 0 ? floor : floor + 1;
    }
    return Math.round(value);
  }

  function evaluateRisk(input, cfg) {
    const auxSet = new Set(cfg.auxiliary_webhook_errors || ["endpoint_analytics_offline"]);
    const errorsMap = input.errors_by_category || {};
    const webhookRate = Number(input.webhook_failure_rate_pct || 0);
    const salesVar =
      input.sales_variation_pct === null || input.sales_variation_pct === undefined
        ? null
        : Number(input.sales_variation_pct);
    const openTickets = Number(input.open_critical_tickets || 0);
    const seasonalPattern = Boolean(input.seasonal_pattern);

    const criticalWebhookThreshold = Number(cfg.critical_webhook_alert_threshold_pct ?? 10.0);
    const severeSalesThreshold = Number(cfg.severe_sales_drop_threshold_pct ?? -30.0);
    const severeSalesMinScore = Number(cfg.severe_sales_drop_min_score ?? 55);
    const severeTicketsThreshold = Number(cfg.severe_tickets_threshold ?? 3);
    const severeTicketsMinScore = Number(cfg.severe_tickets_min_score ?? 50);

    let failedWh = 0;
    let nonAuxFailed = 0;
    for (const [err, qty] of Object.entries(errorsMap)) {
      const n = Number(qty || 0);
      failedWh += n;
      if (!auxSet.has(err)) {
        nonAuxFailed += n;
      }
    }

    const criticalWebhookRate = failedWh > 0 ? (webhookRate * nonAuxFailed) / failedWh : 0.0;
    const baselineStatus = salesVar !== null ? "ok" : "sem_baseline";

    const webhookSubscore = Math.min(100.0, (webhookRate / cfg.webhook_scale_cap_pct) * 100.0);
    const salesDropPct = salesVar !== null ? Math.max(0.0, -salesVar) : 0.0;
    const salesSubscore = Math.min(100.0, (salesDropPct / cfg.sales_drop_scale_cap_pct) * 100.0);
    const ticketSubscore = Math.min(100.0, (openTickets / cfg.critical_tickets_scale_cap) * 100.0);

    const rawScore = pythonRound(
      cfg.weight_webhook * webhookSubscore +
        cfg.weight_sales * salesSubscore +
        cfg.weight_tickets * ticketSubscore
    );

    const salesIsStable = salesVar !== null && salesVar >= cfg.sales_stable_floor_pct;
    const highWebhookFail = webhookRate >= cfg.webhook_alert_threshold_pct;
    const hasCriticalOrUnknownAlert = criticalWebhookRate >= criticalWebhookThreshold;

    const isTechnicalFalseAlarm =
      highWebhookFail &&
      failedWh > 0 &&
      !hasCriticalOrUnknownAlert &&
      salesIsStable &&
      openTickets === 0;
    const isSeasonalDip =
      seasonalPattern &&
      baselineStatus === "ok" &&
      webhookRate < cfg.seasonal_webhook_max_pct &&
      openTickets === 0;

    let severeFloor = 0;
    if (salesVar !== null && salesVar <= severeSalesThreshold && !seasonalPattern) {
      severeFloor = Math.max(severeFloor, severeSalesMinScore);
    }
    if (openTickets >= severeTicketsThreshold) {
      severeFloor = Math.max(severeFloor, severeTicketsMinScore);
    }

    let riskScore;
    let riskLevel;
    let ruleReason;

    if (isTechnicalFalseAlarm) {
      riskScore = Math.min(rawScore, cfg.false_alarm_max_score);
      riskLevel = "falso_alarme_tecnico";
      ruleReason =
        "Falhas concentradas em endpoint auxiliar (taxa crítica abaixo de 10%), vendas estáveis e zero tickets críticos — rebaixado para evitar fadiga de alertas.";
    } else if (hasCriticalOrUnknownAlert) {
      const impactBonus = Math.min(
        cfg.early_tech_impact_bonus_cap,
        pythonRound(cfg.weight_sales * salesSubscore + cfg.weight_tickets * ticketSubscore)
      );
      riskScore = Math.max(rawScore, cfg.early_tech_alert_min_score + impactBonus, severeFloor);
      if (riskScore >= cfg.risk_high_threshold) {
        riskLevel = "alto";
        ruleReason =
          "Falha técnica crítica/desconhecida combinada com impacto comercial ou tickets abertos eleva a conta para prioridade máxima.";
      } else if ((salesIsStable || baselineStatus === "sem_baseline") && openTickets === 0) {
        riskLevel = "alerta_tecnico_precoce";
        ruleReason =
          "Taxa de falhas críticas ou desconhecidas (>=10%) antes mesmo das vendas caírem — visível para ação técnica preventiva.";
      } else {
        riskLevel = "medio";
        ruleReason =
          "Falha técnica crítica já acompanhada de ticket aberto ou queda inicial de vendas — score sobe de forma monotônica (nunca perde visibilidade).";
      }
    } else if (isSeasonalDip) {
      riskScore = Math.min(rawScore, cfg.seasonal_dip_max_score);
      riskLevel = "baixo_sazonalidade";
      ruleReason =
        "Queda isolada em conta com padrão sazonal simulado, sem falhas relevantes de webhook e sem tickets críticos.";
    } else {
      const effectiveScore = Math.max(rawScore, severeFloor);
      if (baselineStatus === "sem_baseline" && effectiveScore < cfg.risk_medium_threshold) {
        riskScore = effectiveScore;
        riskLevel = "sem_baseline";
        ruleReason =
          "Conta sem histórico de vendas nos 21 dias anteriores — o sistema evita classificar como estável por falta de referência.";
      } else {
        riskScore = effectiveScore;
        if (riskScore >= cfg.risk_high_threshold) {
          riskLevel = "alto";
          ruleReason =
            "Combinação de queda de vendas e/ou tickets críticos ultrapassou o limiar de risco alto (>=70).";
        } else if (riskScore >= cfg.risk_medium_threshold) {
          riskLevel = "medio";
          ruleReason =
            severeFloor > rawScore
              ? "Piso de severidade acionado por sinal forte isolado (queda >=30% sem sazonalidade ou >=3 tickets críticos abertos)."
              : "Sinais combinados atingiram o limiar de atenção comercial (>=50).";
        } else {
          riskLevel = "baixo";
          ruleReason = "Operação dentro dos parâmetros normais nos últimos 7 dias.";
        }
      }
    }

    riskScore = Math.max(0, Math.min(100, parseInt(riskScore, 10)));

    return {
      risk_score: riskScore,
      risk_level: riskLevel,
      rule_reason: ruleReason,
      subscores: {
        webhook: Math.round(webhookSubscore),
        sales: Math.round(salesSubscore),
        tickets: Math.round(ticketSubscore),
      },
    };
  }

  return { evaluateRisk, pythonRound };
});