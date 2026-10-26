import type { Server } from "node:http";
import type { ClawWebLifecycle } from "./bootstrap.js";

type ShutdownSignal = "SIGINT" | "SIGTERM";

export type ShutdownHooks = {
  signals: {
    on(signal: ShutdownSignal, listener: () => void): void;
    off(signal: ShutdownSignal, listener: () => void): void;
  };
  exit(code: 0 | 1): void;
  logError(error: unknown): void;
};

/** The CLI is the sole signal owner; bootstraps return stop() instead. */
export function registerShutdown(lifecycle: ClawWebLifecycle | void, hooks: ShutdownHooks): () => void {
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    try {
      await lifecycle?.stop?.();
    } catch (error) {
      hooks.logError(error);
      hooks.exit(1);
      return;
    }
    hooks.exit(0);
  };
  const onSignal = () => { void stop(); };
  // Keep both listeners installed during draining: once() would restore the
  // default termination behavior if the same signal arrives a second time.
  hooks.signals.on("SIGINT", onSignal);
  hooks.signals.on("SIGTERM", onSignal);
  return () => {
    hooks.signals.off("SIGINT", onSignal);
    hooks.signals.off("SIGTERM", onSignal);
  };
}

/** Drain HTTP before dependent resources; a forced close is a shutdown failure. */
export function closeHttpServer(server: Server, timeoutMs = 10_000): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    let settled = false;
    const deadline = setTimeout(() => {
      if (settled) return;
      // Claim the failure before forcing connections closed: close() may invoke
      // its callback as a result, but that must never turn a timeout into success.
      settled = true;
      try {
        server.closeAllConnections();
      } catch (error) {
        reject(error);
        return;
      }
      reject(new Error(`HTTP server shutdown timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    try {
      server.close((error) => {
        if (settled) return;
        settled = true;
        clearTimeout(deadline);
        if (error) reject(error);
        else resolve();
      });
    } catch (error) {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      reject(error);
    }
  });
}
