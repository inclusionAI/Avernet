import type { BotRuntime, HttpInput, ResolvedBotTarget } from "./contracts.js";
import { BotRuntimeError } from "./errors.js";

export type EngineOperation = Pick<HttpInput, "method" | "path" | "body" | "upload">;
/** Engine interfaces, reached through the existing runtime connection. No product OpenAPI or user delegation. */
export class BotEngineOperations {
  constructor(private readonly runtime: Pick<BotRuntime, "request">) {}
  static capabilities(engine: string | undefined) {
    return engine === "teclaw" ? {
      reads: ["POST /api/v1/file/read", "POST /api/v1/file/list", "GET /api/v1/bot/cron[/{id}[/runs]]"],
      writes: ["POST /api/v1/file/upload (multipart)", "POST /api/v1/file/remove", "POST /api/v1/file/skill-package (multipart)", "POST /api/v1/bot/apply", "POST|PUT|DELETE /api/v1/bot/cron[/{id}[/run]]"],
      shell: false,
    } : {
      reads: ["GET /api/engine/status", "GET /api/engine/capabilities", "GET /api/mcp[/{code}]", "POST /api/file/read", "POST /api/file/list", "GET /api/cron[/{id}[/runs]]"],
      writes: ["POST /api/engine/restart", "POST|PUT|DELETE /api/mcp[/{code}]", "POST /api/mcp/{code}/start|stop|restart", "POST /api/file/upload (multipart)", "POST /api/file/remove", "POST /api/skills/local/apply (multipart)", "POST /api/skills/mappings/apply", "POST|PUT|DELETE /api/cron[/{id}[/run]]"],
      shell: true,
    };
  }
  static validate(engine: string | undefined, input: EngineOperation, write: boolean) {
    if (!["openclaw", "claude_code", "teclaw"].includes(engine ?? "openclaw")) throw new BotRuntimeError(422, "unsupported_engine", "Engine operation unavailable");
    if (typeof input.path !== "string" || !input.path.startsWith("/") || /[\\\s\0#%]/u.test(input.path) || input.path.includes("..") || input.path.includes("?")) throw new BotRuntimeError(422, "invalid_engine_path", "Use a declared Engine path without query, traversal or credentials");
    const te = engine === "teclaw", prefix = te ? "/api/v1" : "/api", leaf = "[A-Za-z0-9_.:-]+";
    const cron = new RegExp(`^${prefix}/${te ? "bot/" : ""}cron(?:/${leaf}(?:/runs)?)?$`);
    const fileRead = input.method === "POST" && [prefix + "/file/read", prefix + "/file/list"].includes(input.path);
    const read = fileRead || input.method === "GET" && (cron.test(input.path) || !te && (new RegExp(`^/api/mcp(?:/${leaf})?$`).test(input.path) || ["/api/engine/status", "/api/engine/capabilities"].includes(input.path)));
    const mutation = !read && (input.method === "POST" && [prefix + "/file/upload", prefix + "/file/remove", ...(te ? ["/api/v1/bot/apply", "/api/v1/file/skill-package"] : ["/api/engine/restart", "/api/skills/local/apply", "/api/skills/mappings/apply"])].includes(input.path)
      || input.method === "POST" && (input.path === `${prefix}/${te ? "bot/" : ""}cron` || new RegExp(`^${prefix}/${te ? "bot/" : ""}cron/${leaf}/run$`).test(input.path)
        || !te && (input.path === "/api/mcp" || new RegExp(`^/api/mcp/${leaf}/(?:start|stop|restart)$`).test(input.path)))
      || ["PUT", "DELETE"].includes(input.method) && (new RegExp(`^${prefix}/${te ? "bot/" : ""}cron/(?!status$|running$)${leaf}$`).test(input.path)
        || !te && new RegExp(`^/api/mcp/${leaf}$`).test(input.path)));
    if (!(write ? mutation : read)) throw new BotRuntimeError(403, "engine_operation_not_allowed", "Operation is outside the declared read or approved write contract");
    if (input.upload && (!write || ![prefix + "/file/upload", te ? "/api/v1/file/skill-package" : "/api/skills/local/apply"].includes(input.path))) throw new BotRuntimeError(422, "invalid_engine_upload", "Upload is not supported by this operation");
  }
  async execute(target: ResolvedBotTarget, input: EngineOperation, write: boolean) {
    BotEngineOperations.validate(target.activeEngine, input, write);
    const response = await this.runtime.request({ target, ...input, timeoutMs: 60000 });
    const body = response.body as { success?: boolean; code?: number; buserviceErrorCode?: string } | null;
    const success = response.status >= 200 && response.status < 300 && body?.success !== false && (body?.code === undefined || Number(body.code) === 0) && !body?.buserviceErrorCode;
    return { status: success ? "success" : write && response.status >= 500 ? "unknown" : "failed", httpStatus: response.status, body: response.body };
  }
}
