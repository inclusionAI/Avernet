import { MonitoringError, type MonitoringApi, type MonitoringStore, type MonitoringTarget } from "./contracts.js";
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
      // Reapply trusted directory scope without collapsing same-name defaults or using aliases.
      const items: MonitoringTarget[] = [];
      for (const identity of await store.listTargets()) {
        try { items.push(await resolver.resolveIdentity(identity)); }
        catch (error) {
          if (!(error instanceof MonitoringError) || error.code !== "BOT_IDENTITY_UNAVAILABLE") throw error;
        }
      }
      return { items };
    },
    async reportDiagnosis(input, key) {
      const event = parseDiagnosis(input, key);
      const inserted = await store.insertDiagnosis({ wire: event, target: await resolver.resolveReport(event, event.engine) }, now());
      return { accepted: true, stored: true, eventId: event.eventId, botId: event.botId, entityId: event.entityId, env: event.env, duplicate: !inserted };
    },
    async reportCheck(input) {
      const receivedAt = now();
      const check = parseCheck(input, receivedAt);
      return { accepted: true, botId: check.botId, entityId: check.entityId, env: check.env, applied: await store.applyCheck({ wire: check, target: await resolver.resolveReport(check, check.engine) }, receivedAt) };
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
