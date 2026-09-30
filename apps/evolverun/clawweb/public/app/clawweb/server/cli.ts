#!/usr/bin/env node
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { configureClawWebRuntimeConfig } from "@avernet/clawweb-shared/server/db";
import type { ClawWebBootstrapModule, ClawWebLifecycle } from "./bootstrap.js";
import { loadClawWebConfigFiles, mergeClawWebConfig } from "./config-loader.js";
import { resolveMachineEnvironment } from "./environment.js";

type CliOptions = { bootstrap?: string; env?: string; config?: string };

function parseOptions(args: readonly string[]): CliOptions {
  const options: CliOptions = {};
  for (let index = 0; index < args.length; index += 1) {
    const name = args[index];
    const value = args[index + 1];
    if ((name === "--bootstrap" || name === "--env" || name === "--config") && value) {
      if (name === "--bootstrap") options.bootstrap = value;
      if (name === "--env") options.env = value;
      if (name === "--config") options.config = value;
      index += 1;
      continue;
    }
    throw new Error(`Unknown or incomplete ClawWeb option: ${name}`);
  }
  return options;
}

async function main(): Promise<void> {
  const options = parseOptions(process.argv.slice(2));
  const environment = resolveMachineEnvironment(process.env, options.env);
  const profile = options.bootstrap ? "internal" : "public";
  const appRoot = process.env.CLAWWEB_PUBLIC_CONFIG_ROOT?.trim()
    ? resolve(process.env.CLAWWEB_PUBLIC_CONFIG_ROOT)
    : resolve(dirname(fileURLToPath(import.meta.url)), "../../../../..");
  const baseConfigSources = [
    resolve(appRoot, "configs/application-default.yaml"),
    resolve(appRoot, `configs/application-${environment === "dev" ? "dev" : environment}.yaml`),
  ];
  if (options.config && !existsSync(resolve(options.config))) {
    throw new Error(`ClawWeb config not found: ${resolve(options.config)}`);
  }
  const existingSources = baseConfigSources.filter((path) => existsSync(path));
  const configOverride = options.config ? loadClawWebConfigFiles([resolve(options.config)]) : undefined;
  const config = mergeClawWebConfig(loadClawWebConfigFiles(existingSources), configOverride ?? {});
  configureClawWebRuntimeConfig(config);

  const bootstrapSpecifier = options.bootstrap
    ? pathToFileURL(createRequire(resolve(process.cwd(), "package.json")).resolve(options.bootstrap)).href
    : pathToFileURL(resolve(dirname(fileURLToPath(import.meta.url)), "public-bootstrap.js")).href;
  const loaded = await import(bootstrapSpecifier) as Partial<ClawWebBootstrapModule>;
  if (typeof loaded.createClawWebBootstrap !== "function") {
    throw new Error(`ClawWeb bootstrap does not export createClawWebBootstrap: ${bootstrapSpecifier}`);
  }
  const bootstrap = await loaded.createClawWebBootstrap();
  const lifecycle = await bootstrap.start({
    environment,
    profile,
    config,
    configOverride,
    configSources: [...existingSources, ...(options.config ? [resolve(options.config)] : [])],
  });
  registerShutdown(lifecycle);
}

function registerShutdown(lifecycle: ClawWebLifecycle | void): void {
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    await lifecycle?.stop?.();
    process.exit(0);
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
}

main().catch((error) => {
  console.error("[clawweb] Failed to start server:", error);
  process.exit(1);
});
