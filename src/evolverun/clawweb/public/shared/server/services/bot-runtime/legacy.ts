/** Compatibility surface for existing Repair/Evolve callers. New business code uses BotRuntime. */
export * from "./internal/arca-command-transport.js";
export * from "./internal/baas-command-transport.js";
export { buildRuntimeUserCommand } from "./internal/shell-command.js";
