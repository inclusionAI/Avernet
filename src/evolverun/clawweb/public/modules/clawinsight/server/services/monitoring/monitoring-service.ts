import { MonitoringError, type MonitoringApi, type MonitoringStore } from "./contracts.js";
import { id, parseCheck, parseDiagnosis, parseQuery } from "./validation.js";

export function createMonitoringService(
  store: MonitoringStore, staleSeconds: number, now: () => number = Date.now,
): MonitoringApi {
  async function observedBot(botId: string) {
    const state = await store.readStatus(id(botId));
    if (!state.check && state.count === 0) throw new MonitoringError("BOT_NOT_FOUND", "尚未收到该 Bot 的监控数据。");
    return state;
  }
  return {
    bots: async () => ({ items: await store.listBots() }),
    async reportDiagnosis(input, key) {
      const event = parseDiagnosis(input, key);
      const inserted = await store.insertDiagnosis(event, now());
      return { accepted: true, stored: true, eventId: event.eventId, duplicate: !inserted };
    },
    async reportCheck(input) {
      const receivedAt = now();
      const check = parseCheck(input, receivedAt);
      return { accepted: true, botId: check.botId, applied: await store.applyCheck(check, receivedAt) };
    },
    async status(botId) {
      const { check, count } = await observedBot(botId);
      const stale = !check || now() - Date.parse(check.checkedAt) > staleSeconds * 1000;
      return { botId, status: check?.status === "PAUSED" ? "PAUSED" : stale ? "UNKNOWN" : check!.status,
        lastSuccessfulCheckAt: check?.lastSuccessfulCheckAt ?? null, diagnosisCount: count };
    },
    async diagnoses(botId, query) {
      const parsed = parseQuery(query);
      await observedBot(botId);
      return store.listDiagnoses(botId, parsed);
    },
  };
}
