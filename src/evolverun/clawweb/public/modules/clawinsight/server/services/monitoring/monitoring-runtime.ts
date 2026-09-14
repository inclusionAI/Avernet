/** Composition root: configuration and the existing shared database, no new connection. */
import { getRepositories, type IDatabase } from "@avernet/clawweb-shared/server/db";
import { MonitoringRepository } from "../../repositories/monitoring-repository.js";
import { type MonitoringApi, type MonitoringBot, MonitoringError } from "./contracts.js";
import { createMonitoringService } from "./monitoring-service.js";
import { id, oneOf, record } from "./validation.js";

export type MonitoringRuntime = { service: MonitoringApi | null };
export function createMonitoringRuntime(
  getDb: () => IDatabase = () => getRepositories().db,
  env: Record<string, string | undefined> = process.env,
  now: () => number = Date.now,
): MonitoringRuntime {
  try {
    if (env.CLAWWEB_MONITORING_ENABLED !== "true") return { service: null };
    const parsed: unknown = JSON.parse(env.CLAWWEB_MONITORING_BOTS_JSON ?? "null");
    if (!Array.isArray(parsed) || parsed.length === 0) throw new Error("Missing bot list");
    const bots: MonitoringBot[] = parsed.map((input) => {
      const v = record(input, ["botId", "engine", "paused"]);
      if (v.paused !== undefined && typeof v.paused !== "boolean") throw new Error("Invalid paused flag");
      return { botId: id(v.botId), engine: oneOf(v.engine, ["OC", "TE"]), paused: v.paused === true };
    });
    if (new Set(bots.map((b) => b.botId)).size !== bots.length) throw new Error("Duplicate bot");
    const rawSeconds = env.CLAWWEB_MONITORING_STALE_SECONDS ?? "300";
    const seconds = Number(rawSeconds);
    if (!/^[1-9]\d*$/.test(rawSeconds) || !Number.isSafeInteger(seconds) || !Number.isSafeInteger(seconds * 1000)) throw new Error("Invalid stale seconds");
    // Resolve lazily: listing bots does not require storage; status/history queries require storage.
    let repository: MonitoringRepository | null = null;
    const store = () => {
      try { return repository ??= new MonitoringRepository(getDb()); }
      catch { throw new MonitoringError("NOT_READY", "监控数据库尚未初始化。"); }
    };
    return {
      service: createMonitoringService({
        insertDiagnosis: (...args) => store().insertDiagnosis(...args),
        applyCheck: (...args) => store().applyCheck(...args),
        readStatus: (...args) => store().readStatus(...args),
        listDiagnoses: (...args) => store().listDiagnoses(...args),
      }, bots, seconds, now),
    };
  } catch {
    // Invalid configuration disables only monitoring. Do not log configuration values or credentials.
    return { service: null };
  }
}
