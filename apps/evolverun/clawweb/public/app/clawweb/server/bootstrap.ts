import type { ClawWebConfig } from "./config-loader.js";
import type { ClawWebMachineEnvironment } from "./environment.js";

export type ClawWebStartContext = {
  environment: ClawWebMachineEnvironment;
  profile: "public" | "internal";
  config: ClawWebConfig;
  configOverride?: ClawWebConfig;
  configSources: readonly string[];
};

export type ClawWebLifecycle = {
  stop?: () => Promise<void> | void;
};

export type ClawWebBootstrap = {
  start(context: ClawWebStartContext): Promise<ClawWebLifecycle | void>;
};

export type ClawWebBootstrapModule = {
  createClawWebBootstrap(): ClawWebBootstrap | Promise<ClawWebBootstrap>;
};
