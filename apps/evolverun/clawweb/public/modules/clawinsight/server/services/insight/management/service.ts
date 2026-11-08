import { createHash } from "node:crypto";
import type { ImprovementView } from "../contracts.js";
import { assignOwnerGovernanceSourceType, restoredGovernanceSourceType, rejectedGovernanceSourceType } from "../governance-item.js";
import { managementActions, ManagementError, type ManagementActor, type ManagementCommand, type ManagementEvent } from "./contracts.js";

export type ManagementSnapshot = Pick<ImprovementView, "improvementId" | "version" | "status" | "title" | "sourceType" | "ownerUserId" | "botId" | "userGuidance">;
export type ManagementChange = { status: string; title: string; sourceType: string; event: ManagementEvent };
export interface ManagementStore {
  get(id: number): Promise<ImprovementView | null>;
  list(input: { ownerUserId?: string; botId?: string; status?: string; cursor?: string; pageSize: number }): Promise<{ items: ImprovementView[]; nextCursor: string | null }>;
  apply(id: number, command: ManagementCommand, actor: ManagementActor, fingerprint: string,
    decide: (item: ManagementSnapshot) => ManagementChange): Promise<{ improvement: ImprovementView; receipt: ManagementEvent; replayed: boolean }>;
}
function invalid(message: string): never { throw new ManagementError("INVALID_ARGUMENT", message, 400); }
export function parseManagementCommand(value: unknown): ManagementCommand {
  if (!value || typeof value !== "object" || Array.isArray(value)) invalid("操作必须是对象");
  const body = value as Record<string, unknown>;
  const allowed = ["requestId", "version", "action", "reason", "title", "note", "suggestedAction"];
  if (Object.keys(body).some(key => !allowed.includes(key))) invalid("包含不支持的字段");
  if (typeof body.requestId !== "string" || !/^[A-Za-z0-9_.:-]{1,128}$/.test(body.requestId)) invalid("requestId 不合法");
  if (typeof body.version !== "number" || !Number.isSafeInteger(body.version) || body.version < 1) invalid("version 必须是正整数");
  if (!managementActions.includes(body.action as ManagementCommand["action"])) invalid("action 不支持");
  if (typeof body.reason !== "string" || !body.reason.trim() || body.reason.length > 1000) invalid("reason 必填且不超过1000字符");
  for (const key of ["title", "note", "suggestedAction"] as const) {
    if (body[key] !== undefined && (typeof body[key] !== "string" || (body[key] as string).length > (key === "title" ? 256 : 5000))) invalid(`${key} 不合法`);
  }
  if (body.title !== undefined && !(body.title as string).trim()) invalid("title 不能为空");
  if (body.action !== "edit" && ["title", "note", "suggestedAction"].some(key => body[key] !== undefined)) invalid("编辑字段仅用于 edit 操作");
  if (body.action === "edit" && ["title", "note", "suggestedAction"].every(key => body[key] === undefined)) invalid("edit 至少需要一个编辑字段");
  return { requestId: body.requestId, version: body.version, action: body.action as ManagementCommand["action"], reason: body.reason.trim(),
    ...(body.title !== undefined ? { title: (body.title as string).trim() } : {}),
    ...(body.note !== undefined ? { note: body.note as string } : {}),
    ...(body.suggestedAction !== undefined ? { suggestedAction: body.suggestedAction as string } : {}) };
}
export class InsightManagementService {
  constructor(private readonly store: ManagementStore) {}
  async get(id: number) {
    const item = await this.store.get(id);
    if (!item) throw new ManagementError("NOT_FOUND", "改进项不存在", 404);
    return item;
  }
  list(input: Parameters<ManagementStore["list"]>[0]) { return this.store.list(input); }
  execute(id: number, value: unknown, actor: ManagementActor) {
    if (!Number.isSafeInteger(id) || id < 1) invalid("improvementId 不合法");
    const command = parseManagementCommand(value);
    const fingerprint = createHash("sha256").update(JSON.stringify({ command, actor })).digest("hex");
    return this.store.apply(id, command, actor, fingerprint, item => {
      // Management changes records only. It cannot approve a pending candidate or start a repair.
      if (item.status === "PENDING_ADMIN" && command.action !== "edit") {
        throw new ManagementError("APPROVAL_REQUIRED", "待审核项只能编辑；批准或驳回仍走审核接口", 409);
      }
      const target = { close: "RESOLVED", reopen: "ACTIVE", start: "IN_PROGRESS", defer: "ACTIVE", reject: "ARCHIVED", assign_owner: "ACTIVE", edit: item.status };
      const status = target[command.action];
      let sourceType = item.sourceType;
      if (["reopen", "start", "defer", "assign_owner"].includes(command.action)) sourceType = restoredGovernanceSourceType(sourceType) ?? sourceType;
      if (command.action === "reject") sourceType = rejectedGovernanceSourceType(sourceType) ?? sourceType;
      if (command.action === "assign_owner") sourceType = assignOwnerGovernanceSourceType(sourceType) ?? sourceType;
      return { status, title: command.title ?? item.title, sourceType, event: {
        requestId: command.requestId, fingerprint, actor, action: command.action, reason: command.reason,
        beforeStatus: item.status, afterStatus: status, beforeVersion: item.version, afterVersion: item.version + 1,
        at: new Date().toISOString(), ...(command.note !== undefined ? { note: command.note } : {}),
        ...(command.suggestedAction !== undefined ? { suggestedAction: command.suggestedAction } : {}),
      } };
    });
  }
}
