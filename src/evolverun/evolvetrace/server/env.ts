/** Simplified environment helper for Evolvetrace. */
export function getCurrentEnv(): string {
  return process.env.EVOLVETRACE_ENV ?? "dev";
}
