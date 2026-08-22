/**
 * Platform Logger — structured logging for ClawMind platform modules.
 *
 * Uses stderr in MCP mode (stdout is reserved for the MCP protocol).
 * Provides a simple `createLogger(prefix)` factory for consistent prefixes.
 *
 * @module platform/logger
 */

/**
 * Structured logger interface for platform modules.
 *
 * In MCP mode, all output goes to stderr because stdout is the MCP protocol.
 * Future: replace with a proper logging library (pino, winston) when needed.
 */
export interface PlatformLogger {
  info(message: string, ...args: unknown[]): void;
  warn(message: string, ...args: unknown[]): void;
  error(message: string, ...args: unknown[]): void;
}

/**
 * Create a logger with a consistent prefix.
 *
 * Usage:
 * ```typescript
 * const log = createLogger("clawmind:hermes");
 * log.info("Starting server on port %d", 3100);
 * log.warn("API client init failed: %s", err.message);
 * log.error("Fatal error:", err);
 * ```
 *
 * @param prefix - Log prefix (e.g., "clawmind:mcp", "clawmind:hermes")
 */
export function createLogger(prefix: string): PlatformLogger {
  const tag = `[${prefix}]`;
  return {
    info: (message: string, ...args: unknown[]) => {
      console.error(`${tag} ${message}`, ...args);
    },
    warn: (message: string, ...args: unknown[]) => {
      console.warn(`${tag} ${message}`, ...args);
    },
    error: (message: string, ...args: unknown[]) => {
      console.error(`${tag} ${message}`, ...args);
    },
  };
}