import type { Router } from "express";

import { createClawevolveModule } from "../server/create-module.js";
import type { EvolveTaskDefinitionsOptions } from "../server/routes/evolve.js";

export type ClawevolveModuleHealth = {
  status: "healthy" | "unhealthy";
  code?: string;
};

export type ClawevolveLifecycleHooks = {
  migrate?: () => Promise<void>;
  start?: () => Promise<void>;
  stop?: () => Promise<void>;
  health?: () => Promise<ClawevolveModuleHealth>;
};

export type ClawevolveRuntimeModule = {
  readonly id: "clawevolve";
  readonly apiBasePath: "/api/evolve";
  readonly router: Router;
  migrate(): Promise<void>;
  start(): Promise<void>;
  stop(): Promise<void>;
  health(): Promise<ClawevolveModuleHealth>;
};

export type CreateClawevolveRuntimeModuleOptions = {
  taskDefinitions?: EvolveTaskDefinitionsOptions;
  lifecycle?: ClawevolveLifecycleHooks;
};

/**
 * Creates the stable runtime descriptor consumed by both a standalone host and
 * an existing Node.js host. Hooks are intentionally dependency-injected so the
 * public module does not import a concrete host or environment configuration.
 */
export function createClawevolveRuntimeModule(
  options: CreateClawevolveRuntimeModuleOptions = {},
): ClawevolveRuntimeModule {
  const lifecycle = options.lifecycle ?? {};
  return {
    id: "clawevolve",
    apiBasePath: "/api/evolve",
    router: createClawevolveModule(options.taskDefinitions),
    async migrate() {
      await lifecycle.migrate?.();
    },
    async start() {
      await lifecycle.start?.();
    },
    async stop() {
      await lifecycle.stop?.();
    },
    async health() {
      return lifecycle.health?.() ?? { status: "healthy" };
    },
  };
}
