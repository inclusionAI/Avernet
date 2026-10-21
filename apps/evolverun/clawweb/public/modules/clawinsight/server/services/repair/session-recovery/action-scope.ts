import { BotEngineOperations, type EngineOperation } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryError } from "./errors.js";
import type { RecoveryPlanAction } from "./task-contracts.js";
import { assertAgentContainerCommand } from "../common/agent-tools/runtime-tool.js";

export type RecoveryActionScope = { operation: string; target?: string };
const operations = new Set(["file.write", "file.delete", "engine.restart",
  ...["create", "update", "delete", "run"].map(action => `cron.${action}`)]);
function reject(message: string): never { throw new SessionRecoveryError(403, "recovery_action_scope_mismatch", message); }
export function validateActionScope(scope: RecoveryActionScope): void {
  if (scope && (/^(?:mcp|skill)\./.test(scope.operation) || scope.operation === "bot.configure")) reject("MCP 和用户 Skill 变更请列入 Owner 配合事项；不接受整配置投递绕过该限制");
  if (!scope || !operations.has(scope.operation) || scope.target !== undefined && (typeof scope.target !== "string" || !scope.target.trim() || scope.target.length > 1024)) reject("方案操作范围无效");
  if (scope.operation.startsWith("file.") && scope.target) BotEngineOperations.validateFileTarget(scope.target);
  if (["file.write", "file.delete", "cron.update", "cron.delete", "cron.run"].includes(scope.operation) && !scope.target) reject("方案必须明确操作对象");
}
/** Classify the declared Engine contract, without binding parameter values or complete requests. */
export function engineActionScope(request: EngineOperation): RecoveryActionScope {
  const path = request.path.replace(/^\/api\/v1/, "/api").replace(/^\/api\/bot\/cron/, "/api/cron");
  const resource = path.match(/^\/api\/(cron)(?:\/([^/]+))?(?:\/(run|enable|disable))?$/);
  if (resource) {
    const action = request.method === "DELETE" ? "delete" : request.method === "PUT" ? "update" : resource[3] ?? "create";
    return { operation: `${resource[1]}.${["enable", "disable"].includes(action) ? "update" : action}`, ...(resource[2] ? { target: resource[2] } : {}) };
  }
  if (path === "/api/file/upload" || path === "/api/file/remove") {
    const body = request.body as Record<string, unknown> | undefined;
    const target = request.upload?.targetPath ?? body?.target_path;
    if (typeof target !== "string" || !target.startsWith("/") || target.split("/").some(part => part === "..")) reject("文件操作必须声明明确的绝对路径");
    return { operation: path.endsWith("upload") ? "file.write" : "file.delete", target };
  }
  if (path === "/api/engine/restart") return { operation: "engine.restart" };
  return reject("无法识别该接口的业务动作");
}
export function assertActionScope(action: RecoveryPlanAction, request: EngineOperation): void {
  const approved = action.operationScope ?? (action.engineOperation ? engineActionScope(action.engineOperation) : undefined);
  if (!approved) reject("方案未声明该 Engine 业务动作的范围");
  validateActionScope(approved);
  const actual = engineActionScope(request);
  validateActionScope(actual);
  if (approved.operation !== actual.operation || approved.target !== actual.target) reject("实际操作超出已批准的主要动作或操作对象");
}

/** Bind recovery actions to their approved business scope; tool policy is shared with Repair. */
export function validateRecoveryAction(action: RecoveryPlanAction): void {
  const scope = action.operationScope ?? (action.engineOperation ? engineActionScope(action.engineOperation) : undefined);
  if (!scope) reject("Agent 执行动作必须在结构化 operationScope 中声明主要动作和对象；Owner 操作写入 owner_cooperation");
  validateActionScope(scope);
  if (action.engineOperation) assertActionScope(action, action.engineOperation);
  if (action.command) {
    if (scope.operation.startsWith("cron.") || scope.operation === "engine.restart") reject("Cron 和引擎操作必须通过已声明的接口工具执行，不能改用 Shell");
    assertAgentContainerCommand(action.command);
  }
}
