/** Composition root: configuration and the existing shared database, no new connection. */
import { getRepositories, type IDatabase } from "@avernet/clawweb-shared/server/db";
import { MonitoringRepository } from "../../repositories/monitoring-repository.js";
import { type MonitoringApi, MonitoringError } from "./contracts.js";
import { createMonitoringService } from "./monitoring-service.js";

export type MonitoringRuntime = { service: MonitoringApi | null };
export function createMonitoringRuntime(
  getDb: () => IDatabase = () => getRepositories().db,
  env: Record<string, string | undefined> = process.env,
  now: () => number = Date.now,
): MonitoringRuntime {
  try {
    const rawSeconds = env.CLAWWEB_MONITORING_STALE_SECONDS ?? "300";
    const seconds = Number(rawSeconds);
    if (!/^[1-9]\d*$/.test(rawSeconds) || !Number.isSafeInteger(seconds) || !Number.isSafeInteger(seconds * 1000)) throw new Error("Invalid stale seconds");
    // Resolve lazily so unavailable monitoring storage never blocks Host startup.
    let repository: MonitoringRepository | null = null;
    const store = () => {
      try { return repository ??= new MonitoringRepository(getDb()); }
      catch { throw new MonitoringError("NOT_READY", "监控数据库尚未初始化。"); }
    };
    return {
      service: createMonitoringService({
        listBots: () => store().listBots(),
        insertDiagnosis: (...args) => store().insertDiagnosis(...args),
        applyCheck: (...args) => store().applyCheck(...args),
        readStatus: (...args) => store().readStatus(...args),
        listDiagnoses: (...args) => store().listDiagnoses(...args),
      }, seconds, now),
    };
  } catch {
    // Invalid configuration disables only monitoring. Do not log configuration values or credentials.
    return { service: null };
  }
}
