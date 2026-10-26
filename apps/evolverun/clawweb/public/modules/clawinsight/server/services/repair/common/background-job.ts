import type { AgentControlPlaneEnvironment } from "./task-control-plane.js";

/** The existing Backend cache operations, supplied by the host's cache adapter. */
export interface RepairScanCache {
  get(key: string): Promise<string | null>;
  set(key: string, value: string, ttl: number): Promise<void>;
  acquireLock(key: string, ttl: number): Promise<string | null>;
  renewLock(key: string, token: string, ttl: number): Promise<boolean>;
  releaseLock(key: string, token: string): Promise<void>;
}

/** Retain the timer lifecycle; a cache lock and timestamp coordinate deployed hosts. */
export function startRepairBackgroundJob(options: {
  controlPlaneEnvironment: AgentControlPlaneEnvironment;
  job: "agent_execution" | "recovery_maintenance" | "recovery_reporting";
  cache?: RepairScanCache;
  run(isActive: () => Promise<boolean>): Promise<void>;
}): () => Promise<void> {
  const { cache } = options;
  const key = `clawweb:repair:${options.controlPlaneEnvironment}:${options.job}`;
  const lockKey = `lock:${key}`, timestampKey = `${key}:last_ts`;
  let inFlight: Promise<void> | undefined, stopping = false;
  const tick = async () => {
    const token = cache ? await cache.acquireLock(lockKey, 60) : null;
    if (cache && !token) return;
    let lost = false, renewal: Promise<void> | undefined;
    const isActive = async () => {
      if (lost || stopping) return false;
      try { if (cache && await cache.get(lockKey) !== token) lost = true; }
      catch (error) { lost = true; throw error; }
      return !lost && !stopping;
    };
    const heartbeat = cache && token ? setInterval(() => {
      if (renewal || lost || stopping) return;
      renewal = cache.renewLock(lockKey, token, 60).then(held => { if (!held) lost = true; })
        .catch(() => { lost = true; console.error("[repair-background] cache renewal unavailable", options.job); })
        .finally(() => { renewal = undefined; });
    }, 20000).unref() : undefined;
    try {
      if (!await isActive()) return;
      if (cache) {
        const last = Number(await cache.get(timestampKey));
        // Shared cadence prevents staggered timers from scanning in rotation.
        if (last > 0 && Date.now() - last < 20000) return;
        await cache.set(timestampKey, String(Date.now()), 60);
      }
      if (await isActive()) await options.run(isActive);
    } finally {
      if (heartbeat) clearInterval(heartbeat);
      await renewal;
      if (cache && token) await cache.releaseLock(lockKey, token);
    }
  };
  const timer = setInterval(() => {
    if (inFlight || stopping) return;
    inFlight = tick().catch(() => console.error("[repair-background] scan unavailable; will retry", options.job))
      .finally(() => { inFlight = undefined; });
  }, 20000).unref();
  return async () => { stopping = true; clearInterval(timer); await inFlight; };
}
