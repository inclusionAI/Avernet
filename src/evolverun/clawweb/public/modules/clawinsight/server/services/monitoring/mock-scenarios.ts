/** Test/demo data only. No external services, credentials, or production bot identifiers. */
import { CHECK_VERSION, DIAGNOSIS_VERSION, type BotCheck, type DiagnosisEvent, type MonitoringBot } from './contracts.js';

export function createMonitoringMockScenario(batch: string, now: number) {
  const iso = (ms: number) => new Date(ms).toISOString();
  const bots: MonitoringBot[] = [
    { botId: `mock-ui-${batch}-te`, engine: 'TE' },
    { botId: `mock-ui-${batch}-oc`, engine: 'OC' },
    { botId: `mock-ui-${batch}-stale`, engine: 'TE' },
    { botId: `mock-ui-${batch}-paused`, engine: 'OC', paused: true },
  ];
  const events: DiagnosisEvent[] = [];
  for (const bot of bots.slice(0, 2)) {
    for (let i = 0; i < 30; i++) {
      const decision = (['ALERT', 'PASS', 'UNRESOLVED'] as const)[i % 3];
      const id = `${bot.botId}-${i}`;
      events.push({ schemaVersion: DIAGNOSIS_VERSION, eventId: id, diagnosisId: id,
        botId: bot.botId, engine: bot.engine,
        sessionKey: bot.engine === 'OC' ? `agent:main:mock:${i}` : null,
        sessionId: bot.engine === 'TE' ? `mock-session-${Math.floor(i / 3)}` : null,
        traceId: bot.engine === 'TE' ? `mock-trace-${i}` : null,
        // Three days, plus one missing timestamp; supports date filters and null-last ordering.
        occurredAt: i === 29 ? null : iso(now - Math.floor(i / 10) * 86400000 - i * 60000),
        diagnosedAt: iso(now), decision,
        tcFaultLabel: decision === 'ALERT' ? 'TC.MCP.DATA' : null,
        confidence: decision === 'ALERT' ? .86 : null,
        businessProblemCategory: decision === 'ALERT' ? '外部服务异常' : decision === 'PASS' ? '正常' : null,
        businessProblemSubtype: decision === 'ALERT' ? '数据获取失败' : decision === 'PASS' ? '目标已完成' : null,
        systemDiagnosis: decision === 'ALERT' ? '数据查询工具返回空响应，本轮数据获取失败。' : decision === 'PASS' ? '本轮工具调用正常完成。' : '诊断模型暂不可用，无法生成有效结论。',
        businessDiagnosis: decision === 'ALERT' ? '任务未获取到所需数据，尚未完成。' : decision === 'PASS' ? '用户查询任务已完成。' : null,
        handlerName: null, humanIntervention: i % 2 === 0,
      });
    }
  }
  const checks: BotCheck[] = bots.slice(0, 3).map((bot, i) => ({ schemaVersion: CHECK_VERSION,
    botId: bot.botId, engine: bot.engine,
    checkedAt: iso(now - (i === 2 ? 3600000 : 0)),
    lastSuccessfulCheckAt: iso(now - (i === 0 ? 0 : 3600000)),
    status: i === 1 ? 'ERROR' : 'HEALTHY',
  }));
  return { bots, events, checks };
}
