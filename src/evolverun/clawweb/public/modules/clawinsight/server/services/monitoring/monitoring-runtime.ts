/** Composition root: use the existing shared database, with no monitoring environment configuration. */
import { getRepositories, type IDatabase } from "@avernet/clawweb-shared/server/db";
import { MonitoringRepository } from "../../repositories/monitoring-repository.js";
import { type MonitoringApi, MonitoringError } from "./contracts.js";
import { createMonitoringService } from "./monitoring-service.js";

// Status freshness is a fixed policy, not a deployment prerequisite.
const STALE_SECONDS = 300;
export type MonitoringRuntime = { service: MonitoringApi | null };
export function createMonitoringRuntime(
  getDb: () => IDatabase = () => getRepositories().db,
  now: () => number = Date.now,
): MonitoringRuntime {
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
    }, STALE_SECONDS, now),
  };
}
