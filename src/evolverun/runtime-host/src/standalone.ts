import { createRuntimeHost } from "./create-host.js";
import type { RuntimeHost, StandaloneHostOptions } from "./types.js";

export async function startStandaloneHost(
  options: StandaloneHostOptions,
): Promise<RuntimeHost> {
  const host = createRuntimeHost(options);
  await host.start({ port: options.port, hostname: options.hostname });
  if (options.installSignalHandlers !== false) host.installSignalHandlers();
  return host;
}
