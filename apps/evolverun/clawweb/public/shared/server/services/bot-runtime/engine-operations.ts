import type { BotRuntime, HttpInput, ResolvedBotTarget } from "./contracts.js";
import { BotRuntimeError } from "./errors.js";

export type EngineOperation = Pick<HttpInput, "method" | "path" | "body" | "upload"> & { query?: { limit?: number; force?: boolean } };
/** Agent-facing adapter interfaces over the existing runtime connection. */
export class BotEngineOperations {
  constructor(private readonly runtime: Pick<BotRuntime, "request">) {}
  static capabilities(engine: string | undefined) {
    return engine === "teclaw" ? {
      reads: ["POST /api/v1/file/read", "POST /api/v1/file/list", "GET /api/v1/bot/cron[/{id}[/runs]]"],
      writes: ["POST /api/v1/file/upload (multipart)", "POST /api/v1/file/remove", "POST /api/v1/bot/cron", "PUT|DELETE /api/v1/bot/cron/{id}", "POST /api/v1/bot/cron/{id}/run", "POST /api/v1/bot/cron/{id}/enable|disable"],
      shell: false,
    } : {
      reads: ["GET /api/engine/status", "GET /api/mcp[/{code}]", "POST /api/file/read", "POST /api/file/list", "GET /api/cron[/{id}[/runs]]"],
      writes: ["POST /api/engine/restart", "POST /api/file/upload (multipart)", "POST /api/file/remove", "POST /api/cron", "PUT|DELETE /api/cron/{id}", "POST /api/cron/{id}/run"],
      shell: true,
    };
  }
  /** Explicit managed locators; reads remain available through the normal tools. */
  static isManagedPath(path: string): boolean {
    return /(?:^|[\s/'"])(?:skills(?:-pool|-local|-repo)?|skill-center|SKILL\.md|\.?mcp\.json|mcporter\.json|openclaw\.json|teclaw\.json|\.claude\.json|claude_desktop_config\.json|cron)(?:[\s/'"]|$)/i.test(path);
  }
  static validateFileTarget(path: unknown): void {
    if (typeof path !== "string" || !path.startsWith("/") || /[\\\0\r\n]/u.test(path) || path.split("/").some(part => part === "..")) throw new BotRuntimeError(422, "invalid_engine_file_target", "File writes require an explicit absolute target path");
    if (this.isManagedPath(path)) throw new BotRuntimeError(403, "managed_runtime_file", "MCP and Skill changes require Owner action; Cron changes use the declared adapter interfaces");
  }
  static validate(engine: string | undefined, input: EngineOperation, write: boolean) {
    if (!["openclaw", "claude_code", "teclaw"].includes(engine ?? "openclaw")) throw new BotRuntimeError(422, "unsupported_engine", "Engine operation unavailable");
    if (typeof input.path !== "string" || !input.path.startsWith("/") || /[\\\s\0#%]/u.test(input.path) || input.path.includes("..") || input.path.includes("?")) throw new BotRuntimeError(422, "invalid_engine_path", "Use a declared Engine path without query, traversal or credentials");
    const te = engine === "teclaw", prefix = te ? "/api/v1" : "/api", leaf = "[A-Za-z0-9_.:-]+";
    const cron = new RegExp(`^${prefix}/${te ? "bot/" : ""}cron(?:/${leaf}(?:/runs)?)?$`);
    const fileRead = input.method === "POST" && [prefix + "/file/read", prefix + "/file/list"].includes(input.path);
    const read = fileRead || input.method === "GET" && (cron.test(input.path) || !te && (new RegExp(`^/api/mcp(?:/${leaf})?$`).test(input.path) || input.path === "/api/engine/status"));
    const mutation = !read && (input.method === "POST" && [prefix + "/file/upload", prefix + "/file/remove", ...(te ? [] : ["/api/engine/restart"])].includes(input.path)
      || input.method === "POST" && (input.path === `${prefix}/${te ? "bot/" : ""}cron` || new RegExp(`^${prefix}/${te ? "bot/" : ""}cron/${leaf}/run$`).test(input.path)
        || te && new RegExp(`^/api/v1/bot/cron/${leaf}/(?:enable|disable)$`).test(input.path))
      || ["PUT", "DELETE"].includes(input.method) && new RegExp(`^${prefix}/${te ? "bot/" : ""}cron/(?!status$|running$)${leaf}$`).test(input.path));
    if (!(write ? mutation : read)) throw new BotRuntimeError(403, "engine_operation_not_allowed", "Operation is outside the declared read or approved write contract");
    if (te && fileRead) {
      const field = input.path.endsWith("/read") ? "file_path" : "dir_path";
      const path = (input.body as Record<string, unknown> | undefined)?.[field];
      if (typeof path !== "string" || !/^\/(?:workspace|identity|config)(?:\/|$)/.test(path)
        || /[\\\0\r\n]/u.test(path) || path.split("/").includes("..")) {
        throw new BotRuntimeError(422, "invalid_engine_file_read",
          `TE file ${input.path.endsWith("/read") ? "read" : "list"} requires body.${field} in /workspace, /identity or /config; physical host and AIS-local paths are not Engine file paths`);
      }
    }
    if (input.query !== undefined) {
      const query = input.query;
      if (!query || typeof query !== "object" || Array.isArray(query) || Object.keys(query).some(key => !["limit", "force"].includes(key))
        || query.limit !== undefined && (input.method !== "GET" || !new RegExp(`^${prefix}/${te ? "bot/" : ""}cron/${leaf}/runs$`).test(input.path) || !Number.isSafeInteger(query.limit) || query.limit < 1 || query.limit > 1000)
        || query.force !== undefined && (input.method !== "POST" || !input.path.endsWith("/run") || typeof query.force !== "boolean")) throw new BotRuntimeError(422, "invalid_engine_query", "Only cron runs.limit and run.force are supported");
    }
    if (["POST", "PUT"].includes(input.method) && new RegExp(`^${prefix}/${te ? "bot/" : ""}cron(?:/${leaf})?$`).test(input.path)) {
      const body = input.body as Record<string, unknown> | undefined;
      if (body?.schedule !== undefined && typeof body.schedule !== "string" || body?.payload !== undefined) throw new BotRuntimeError(422, "invalid_cron_body", "Cron HTTP uses a schedule expression string and command, not internal schedule/payload objects");
      if (te && body?.enabled !== undefined) throw new BotRuntimeError(422, "invalid_cron_body", "TE cron enable/disable uses POST /api/v1/bot/cron/{id}/enable or /disable");
    }
    if (input.upload && (!write || input.path !== prefix + "/file/upload")) throw new BotRuntimeError(422, "invalid_engine_upload", "Upload is not supported by this operation");
    if (write && [prefix + "/file/upload", prefix + "/file/remove"].includes(input.path)) this.validateFileTarget(input.upload?.targetPath ?? (input.body as Record<string, unknown> | undefined)?.target_path);
  }
  async execute(target: ResolvedBotTarget, input: EngineOperation, write: boolean) {
    BotEngineOperations.validate(target.activeEngine, input, write);
    const { query, ...request } = input;
    const params = new URLSearchParams(Object.entries(query ?? {}).map(([key, value]) => [key, String(value)]));
    const response = await this.runtime.request({ target, ...request, path: input.path + (params.size ? `?${params}` : ""), timeoutMs: 60000 });
    const body = response.body as { success?: boolean; code?: number; buserviceErrorCode?: string } | null;
    const success = response.status >= 200 && response.status < 300 && body?.success !== false && (body?.code === undefined || Number(body.code) === 0) && !body?.buserviceErrorCode;
    return { status: success ? "success" : write && response.status >= 500 ? "unknown" : "failed", httpStatus: response.status, body: response.body };
  }
}
