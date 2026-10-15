import type { EngineOperation } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryError } from "./errors.js";
import type { RecoveryPlanAction } from "./task-contracts.js";

export type RecoveryActionScope = { operation: string; target?: string };
const operations = new Set(["file.write", "file.delete", "skill.install", "skill.configure", "bot.configure", "engine.restart",
  ...["create", "update", "delete", "run"].map(action => `cron.${action}`),
  ...["create", "update", "delete", "start", "stop", "restart"].map(action => `mcp.${action}`)]);
function reject(message: string): never { throw new SessionRecoveryError(403, "recovery_action_scope_mismatch", message); }
export function validateActionScope(scope: RecoveryActionScope): void {
  if (!scope || !operations.has(scope.operation) || scope.target !== undefined && (typeof scope.target !== "string" || !scope.target.trim() || scope.target.length > 1024)) reject("方案操作范围无效");
  if (["file.write", "file.delete", "cron.update", "cron.delete", "cron.run", "mcp.update", "mcp.delete", "mcp.start", "mcp.stop", "mcp.restart"].includes(scope.operation) && !scope.target) reject("方案必须明确操作对象");
}
/** Classify the declared Engine contract, without binding parameter values or complete requests. */
export function engineActionScope(request: EngineOperation): RecoveryActionScope {
  const path = request.path.replace(/^\/api\/v1/, "/api").replace(/^\/api\/bot\/cron/, "/api/cron");
  const resource = path.match(/^\/api\/(cron|mcp)(?:\/([^/]+))?(?:\/(run|start|stop|restart))?$/);
  if (resource) {
    const action = request.method === "DELETE" ? "delete" : request.method === "PUT" ? "update" : resource[3] ?? "create";
    return { operation: `${resource[1]}.${action}`, ...(resource[2] ? { target: resource[2] } : {}) };
  }
  if (path === "/api/file/upload" || path === "/api/file/remove") {
    const body = request.body as Record<string, unknown> | undefined;
    const target = request.upload?.targetPath ?? body?.path;
    if (typeof target !== "string" || !target.startsWith("/") || target.split("/").some(part => part === "..")) reject("文件操作必须声明明确的绝对路径");
    return { operation: path.endsWith("upload") ? "file.write" : "file.delete", target };
  }
  if (["/api/file/skill-package", "/api/skills/local/apply"].includes(path)) return { operation: "skill.install" };
  if (path === "/api/skills/mappings/apply") return { operation: "skill.configure" };
  if (path === "/api/bot/apply") return { operation: "bot.configure" };
  if (path === "/api/engine/restart") return { operation: "engine.restart" };
  return reject("无法识别该接口的业务动作");
}
export function assertActionScope(action: RecoveryPlanAction, request: EngineOperation): void {
  const approved = action.operationScope ?? (action.engineOperation ? engineActionScope(action.engineOperation) : undefined);
  if (!approved) reject("方案未声明该 Engine 业务动作的范围");
  validateActionScope(approved);
  const actual = engineActionScope(request);
  if (approved.operation !== actual.operation || approved.target !== actual.target) reject("实际操作超出已批准的主要动作或操作对象");
}
