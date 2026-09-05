/** Simplified environment helper for Evolvetrace. */
export function getCurrentEnv(): string {
  return process.env.EVOLVETRACE_ENV ?? "dev";
}

/** Additional browser origins allowed to call the Evolvetrace HTTP API. */
export function getCorsAllowedOrigins(): Set<string> {
  return new Set(
    (process.env.CORS_ALLOWED_ORIGINS ?? "")
      .split(",")
      .map((origin) => origin.trim())
      .filter(Boolean),
  );
}
