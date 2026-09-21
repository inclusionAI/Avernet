import { MonitoringError, type MonitoringApi, type MonitoringStore } from "./contracts.js";
import type { MonitoringTargetResolver } from "./directory-contracts.js";
import { id, parseCheck, parseDiagnosis, parseQuery } from "./validation.js";

export function createMonitoringService(
  store: MonitoringStore, resolver: MonitoringTargetResolver, now: () => number = Date.now,
): MonitoringApi {
  async function observedBot(botId: string) {
    const target = await resolver.resolve(id(botId));
    const state = await store.readStatus(target);
    if (!state.check && state.count === 0) throw new MonitoringError("BOT_NOT_FOUND", "尚未收到该 Bot 的监控数据。");
    return { ...state, target };
  }
  return {
    async bots() {
      const ids: string[] = [];
      for (const target of await store.listTargets()) {
        const wireId = await resolver.legacyId(target);
        if (wireId !== null) ids.push(wireId);
      }
      return { items: [...new Set(ids)].sort().map(botId => ({ botId })) };
    },
    async reportDiagnosis(input, key) {
      const event = parseDiagnosis(input, key);
      const inserted = await store.insertDiagnosis({ wire: event, target: await resolver.resolve(event.botId) }, now());
      return { accepted: true, stored: true, eventId: event.eventId, duplicate: !inserted };
    },
    async reportCheck(input) {
      const receivedAt = now();
      const check = parseCheck(input, receivedAt);
      return { accepted: true, botId: check.botId, applied: await store.applyCheck({ wire: check, target: await resolver.resolve(check.botId) }, receivedAt) };
    },
    async status(botId) {
      const { check, count } = await observedBot(botId);
      return { botId, status: check?.status ?? "UNKNOWN",
        lastSuccessfulCheckAt: check?.lastSuccessfulCheckAt ?? null, diagnosisCount: count };
    },
    async diagnoses(botId, query) {
      const parsed = parseQuery(query);
      const { target } = await observedBot(botId);
      const page = await store.listDiagnoses(target, parsed);
      return { ...page, botId, items: page.items.map(item => ({ ...item, botId })) };
    },
  };
}
