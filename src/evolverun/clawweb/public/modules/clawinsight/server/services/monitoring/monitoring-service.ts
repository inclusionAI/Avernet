import { MonitoringError, type MonitoringApi, type MonitoringBot, type MonitoringStore } from "./contracts.js";
import { parseCheck, parseDiagnosis, parseQuery } from "./validation.js";

export function createMonitoringService(
  store: MonitoringStore, bots: readonly MonitoringBot[], staleSeconds: number, now: () => number = Date.now,
): MonitoringApi {
  function bot(id: string, engine?: string): MonitoringBot {
    const found = bots.find((item) => item.botId === id);
    if (!found || (engine !== undefined && found.engine !== engine)) {
      throw new MonitoringError(engine === undefined ? "BOT_NOT_FOUND" : "BOT_NOT_ALLOWED", "Bot 未配置或引擎不匹配。");
    }
    return found;
  }
  return {
    bots: () => ({ items: bots.map(({ botId }) => ({ botId })) }),
    async reportDiagnosis(input, key) {
      const event = parseDiagnosis(input, key);
      bot(event.botId, event.engine);
      const inserted = await store.insertDiagnosis(event, now());
      return { accepted: true, stored: true, eventId: event.eventId, duplicate: !inserted };
    },
    async reportCheck(input) {
      const receivedAt = now();
      const check = parseCheck(input, receivedAt);
      bot(check.botId, check.engine);
      return { accepted: true, botId: check.botId, applied: await store.applyCheck(check, receivedAt) };
    },
    async status(botId) {
      const config = bot(botId);
      const { check, count } = await store.readStatus(botId);
      const stale = !check || now() - Date.parse(check.checkedAt) > staleSeconds * 1000;
      return { botId, status: config.paused ? "PAUSED" : stale ? "UNKNOWN" : check!.status,
        lastSuccessfulCheckAt: check?.lastSuccessfulCheckAt ?? null, diagnosisCount: count };
    },
    async diagnoses(botId, query) {
      bot(botId);
      return store.listDiagnoses(botId, parseQuery(query));
    },
  };
}
