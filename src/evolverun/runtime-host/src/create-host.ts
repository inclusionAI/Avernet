import express from "express";
import { existsSync } from "node:fs";
import type { Server } from "node:http";
import { join, resolve } from "node:path";

import type {
  ModuleHealth,
  RuntimeHost,
  RuntimeHostOptions,
  RuntimeListenOptions,
  RuntimeModule,
} from "./types.js";

const RESERVED_PATHS = new Set(["/health", "/ready"]);

function validateModules(modules: readonly RuntimeModule[]): void {
  const ids = new Set<string>();
  const paths = new Set<string>();

  for (const module of modules) {
    if (!module.id.trim()) throw new Error("Runtime module id must not be empty");
    if (!module.apiBasePath.startsWith("/") || module.apiBasePath === "/") {
      throw new Error(`Invalid API base path for module ${module.id}`);
    }
    if (RESERVED_PATHS.has(module.apiBasePath)) {
      throw new Error(`Reserved API base path for module ${module.id}`);
    }
    if (ids.has(module.id)) throw new Error(`Duplicate runtime module id: ${module.id}`);
    if (paths.has(module.apiBasePath)) {
      throw new Error(`Duplicate runtime module path: ${module.apiBasePath}`);
    }
    ids.add(module.id);
    paths.add(module.apiBasePath);
  }
}

async function readHealth(module: RuntimeModule): Promise<ModuleHealth> {
  try {
    return await module.health();
  } catch {
    return { status: "unhealthy", code: "HEALTH_CHECK_FAILED" };
  }
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolveClose, reject) => {
    server.close((error) => error ? reject(error) : resolveClose());
  });
}

export function createRuntimeHost(options: RuntimeHostOptions): RuntimeHost {
  validateModules(options.modules);

  const app = express();
  const modules = [...options.modules];
  const startedModules: RuntimeModule[] = [];
  let server: Server | null = null;
  let ready = false;
  let transition: Promise<unknown> | null = null;
  let removeSignalHandlers: (() => void) | null = null;

  app.use(express.json({ limit: options.jsonLimit ?? "10mb" }));
  for (const middleware of options.middleware ?? []) app.use(middleware);

  app.get("/health", async (_request, response) => {
    const health = await Promise.all(modules.map(async (module) => ({
      id: module.id,
      ...await readHealth(module),
    })));
    const healthy = health.every((entry) => entry.status === "healthy");
    response.status(healthy ? 200 : 503).json({
      status: healthy ? "healthy" : "unhealthy",
      modules: health,
    });
  });

  app.get("/ready", async (_request, response) => {
    const health = await Promise.all(modules.map(async (module) => ({
      id: module.id,
      ...await readHealth(module),
    })));
    const isReady = ready && health.every((entry) => entry.status === "healthy");
    response.status(isReady ? 200 : 503).json({
      status: isReady ? "ready" : "not_ready",
      modules: health,
    });
  });

  for (const module of modules) app.use(module.apiBasePath, module.router);

  if (options.staticDir) {
    const staticDir = resolve(options.staticDir);
    if (!existsSync(join(staticDir, "index.html"))) {
      throw new Error(`Static directory does not contain index.html: ${staticDir}`);
    }
    app.use(express.static(staticDir, { maxAge: "1d", immutable: false }));
    app.get("{*path}", (request, response) => {
      if (request.path.startsWith("/api/")) {
        response.status(404).json({ error: "Not Found" });
        return;
      }
      response.sendFile(join(staticDir, "index.html"));
    });
  }

  const runtime: RuntimeHost = {
    app,
    modules,
    get ready() {
      return ready;
    },
    get server() {
      return server;
    },
    async start(listenOptions: RuntimeListenOptions = {}) {
      if (transition) throw new Error("Runtime host lifecycle transition already in progress");
      if (server) throw new Error("Runtime host is already started");

      const startTransition = (async () => {
        try {
          for (const module of modules) await module.migrate();
          for (const module of modules) {
            startedModules.push(module);
            await module.start();
          }

          const port = listenOptions.port ?? 3001;
          const hostname = listenOptions.hostname ?? "0.0.0.0";
          server = await new Promise<Server>((resolveListen, reject) => {
            const nextServer = app.listen(port, hostname);
            nextServer.once("listening", () => resolveListen(nextServer));
            nextServer.once("error", reject);
          });
          ready = true;
          return server;
        } catch (error) {
          ready = false;
          for (const module of startedModules.splice(0).reverse()) {
            try {
              await module.stop();
            } catch {
              // Preserve the original startup error.
            }
          }
          throw error;
        }
      })();

      transition = startTransition;
      try {
        return await startTransition;
      } finally {
        transition = null;
      }
    },
    async stop() {
      if (transition) await transition.catch(() => undefined);
      ready = false;
      removeSignalHandlers?.();

      const currentServer = server;
      server = null;
      if (currentServer) await closeServer(currentServer);

      const errors: unknown[] = [];
      for (const module of startedModules.splice(0).reverse()) {
        try {
          await module.stop();
        } catch (error) {
          errors.push(error);
        }
      }
      if (errors.length > 0) throw new AggregateError(errors, "Runtime module shutdown failed");
    },
    installSignalHandlers() {
      if (removeSignalHandlers) return removeSignalHandlers;
      let stopping = false;
      const shutdown = () => {
        if (stopping) return;
        stopping = true;
        void runtime.stop().finally(() => {
          stopping = false;
        });
      };
      process.on("SIGINT", shutdown);
      process.on("SIGTERM", shutdown);
      removeSignalHandlers = () => {
        process.off("SIGINT", shutdown);
        process.off("SIGTERM", shutdown);
        removeSignalHandlers = null;
      };
      return removeSignalHandlers;
    },
  };

  return runtime;
}
