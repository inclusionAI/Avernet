import { randomUUID } from "node:crypto";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { RepairSchedulerRepository, type RepairBackgroundJob } from "../../../repositories/repair/repair-scheduler-repository.js";
import type { AgentControlPlaneEnvironment } from "./task-control-plane.js";

/** Preserve the existing timer lifecycle while coordinating each scan in the shared database. */
export function startRepairBackgroundJob(options: {
  db: IDatabase; controlPlaneEnvironment: AgentControlPlaneEnvironment; job: RepairBackgroundJob;
  run(isActive: () => Promise<boolean>): Promise<void>;
}): () => Promise<void> {
  const leases = new RepairSchedulerRepository(options.db);
  let inFlight: Promise<void> | undefined, stopping = false;
  const tick = async () => {
    const lease = { environment: options.controlPlaneEnvironment, job: options.job, owner: randomUUID() };
    if (!await leases.claim(lease)) return;
    let lost = false, renewal: Promise<void> | undefined;
    const isActive = async () => {
      if (lost || stopping) return false;
      if (!await leases.isHeld(lease)) lost = true;
      return !lost && !stopping;
    };
    const heartbeat = setInterval(() => {
      if (renewal || lost || stopping) return;
      renewal = leases.renew(lease).then(held => { if (!held) lost = true; })
        .catch(() => { lost = true; console.error("[repair-background] lease renewal unavailable", options.job); })
        .finally(() => { renewal = undefined; });
    }, 20000);
    heartbeat.unref();
    try { if (await isActive()) await options.run(isActive); }
    finally {
      clearInterval(heartbeat);
      await renewal;
      await leases.complete(lease);
    }
  };
  const timer = setInterval(() => {
    if (inFlight || stopping) return;
    inFlight = tick().catch(() => console.error("[repair-background] scan unavailable; will retry", options.job))
      .finally(() => { inFlight = undefined; });
  }, 20000);
  timer.unref();
  return async () => { stopping = true; clearInterval(timer); await inFlight; };
}
