import type { Express, RequestHandler, Router } from "express";
import type { Server } from "node:http";

export type ModuleHealthStatus = "healthy" | "unhealthy";

export type ModuleHealth = {
  status: ModuleHealthStatus;
  code?: string;
};

/**
 * Public lifecycle contract implemented by an Avernet runtime module.
 *
 * The host invokes hooks in migrate -> start -> health order. Stop hooks run
 * in reverse registration order.
 */
export interface RuntimeModule {
  readonly id: string;
  readonly apiBasePath: `/${string}`;
  readonly router: Router;
  migrate(): Promise<void>;
  start(): Promise<void>;
  stop(): Promise<void>;
  health(): Promise<ModuleHealth>;
}

export type RuntimeHostOptions = {
  modules: readonly RuntimeModule[];
  middleware?: readonly RequestHandler[];
  jsonLimit?: string;
  staticDir?: string;
};

export type RuntimeListenOptions = {
  port?: number;
  hostname?: string;
};

export type RuntimeHost = {
  readonly app: Express;
  readonly modules: readonly RuntimeModule[];
  readonly ready: boolean;
  readonly server: Server | null;
  start(options?: RuntimeListenOptions): Promise<Server>;
  stop(): Promise<void>;
  installSignalHandlers(): () => void;
};

export type StandaloneHostOptions = RuntimeHostOptions & RuntimeListenOptions & {
  installSignalHandlers?: boolean;
};
