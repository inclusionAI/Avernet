/** Source-aware workflow execution; transport selection belongs to the host adapter. */
import { randomUUID } from "node:crypto";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import type { FlowRunRepository } from "../repositories/flow-run-repository.js";
import type { FacadeBindingRepository } from "../repositories/facade-binding-repository.js";
import { resolveRerunCommand } from "./rerun-command.js";

export type LifecycleStage = "draft" | "verify" | "online";

/** BaaS routes on "<bot_id>:<owner_staff_no>"; a bare id is rejected before lookup. */
export const BAAS_BOT_ID_PATTERN = /^[^:\s]+:[^:\s]+$/;

/** Outbound delivery contract, structurally compatible with the BaaS adapter. */
export type AutoHealRunSender = (input: {
  botId: string;
  sessionKey: string;
  sessionId: string | null;
  message: string;
  /** Service Bots only; omitted for personal Bots so BaaS does not receive bot_options. */
  lifecycleStage?: LifecycleStage;
}) => Promise<{ ok: boolean; messageId?: string; sessionId?: string; error?: string; tokenExpired?: boolean }>;

export interface AutoHealRunDependencies {
  flowRunRepo: FlowRunRepository | null;
  botPermRepo: BotWorkflowPermissionRepository | null;
  sendIntervention: AutoHealRunSender;
  /** Supplies the slash command for runs whose input_json records no command. */
  facadeRepo?: FacadeBindingRepository | null;
}

export interface AutoHealRunInput {
  workflowId: string;
  sourceFlowId: string;
  actorId: string;
  /**
   * Recorded for the acceptance audit only. The trigger is the slash command
   * resolved from the source run, so these params do not reach the Bot.
   */
  params?: Record<string, unknown>;
  isAdmin?: boolean;
  userName?: string;
}

export interface AutoHealRunResult {
  ok: true;
  workflowId: string;
  /** BaaS message id for this delivery. It is NOT a ClawMind flow id: no
   *  flow_runs row exists yet, and one may never appear if the Bot times out.
   *  Never persist this into a flow id column. */
  messageId: string;
  sessionId: string | null;
}

export class AutoHealRunError extends Error {
  constructor(readonly code: "bad_request" | "unauthorized" | "forbidden" | "unavailable" | "not_found"
    | "invalid_source" | "delivery_failed" | "token_expired", message: string) {
    super(message);
  }
}

function parseObject(json: string | null): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(json ?? "null");
    return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

/**
 * Resolve the BaaS lifecycle stage for the source run.
 *
 * `bot_options.lifecycle_stage` is a service-Bot option: it selects which of the
 * three service-Bot stages the message reaches. A personal Bot has no stage, so
 * the field must be omitted instead of defaulted, which is why this returns
 * undefined rather than "online".
 */
export function resolveSourceStage(json: string | null): LifecycleStage | undefined {
  if (json == null || json === "") return undefined;
  // Extract only BOT_TYPE and STAGE. Never forward credentials, transport
  // settings, or parser diagnostics.
  const credentials = parseObject(json);
  if (!credentials) {
    // Corrupt or non-object credentials leave the Bot type unknowable, so fail
    // closed instead of sending the run to an arbitrary stage.
    throw new AutoHealRunError("invalid_source", "Source run credentials must be a JSON object with BOT_TYPE and STAGE");
  }
  if (credentials.BOT_TYPE !== "service") return undefined;
  const stage = credentials.STAGE;
  if (stage === undefined) return "online";
  const normalized = typeof stage === "string" ? stage.trim() : stage;
  if (normalized === "draft" || normalized === "verify" || normalized === "online") return normalized;
  throw new AutoHealRunError("invalid_source", "Source run STAGE must be draft, verify, or online");
}

export class AutoHealRunService {
  constructor(private readonly deps: AutoHealRunDependencies) {}

  async run(input: AutoHealRunInput): Promise<AutoHealRunResult> {
    const { workflowId, sourceFlowId, actorId } = input;
    if (!actorId?.trim()) throw new AutoHealRunError("unauthorized", "User identity required");
    if (!workflowId?.trim() || !sourceFlowId?.trim()) {
      throw new AutoHealRunError("bad_request", "workflowId and sourceFlowId are required");
    }
    const { flowRunRepo, botPermRepo } = this.deps;
    if (!botPermRepo) throw new AutoHealRunError("forbidden", "Permission service not available");
    if (!flowRunRepo) throw new AutoHealRunError("unavailable", "Run repository not available");
    if (input.isAdmin !== true) {
      const [canExecute, canEdit] = await Promise.all([
        botPermRepo.hasExecutePermission(workflowId, actorId),
        botPermRepo.hasEditPermission(workflowId, actorId),
      ]);
      if (!canExecute && !canEdit) throw new AutoHealRunError("forbidden", "No execute or edit permission for this workflow");
    }

    const run = await flowRunRepo.findFullByFlowId(sourceFlowId);
    if (!run) throw new AutoHealRunError("not_found", "Source run not found");
    if (run.workflow_id !== workflowId) throw new AutoHealRunError("bad_request", "Source run does not match workflowId");
    const botId = run.origin_bot_id;
    const identity = typeof botId === "string" ? /^([^:\s*]+):([^:\s*]+)$/.exec(botId) : null;
    if (!identity) throw new AutoHealRunError("invalid_source", "Source run requires origin_bot_id in botId:ownerId format");
    // Visibility and workflow grants do not authorize sending as someone else's Bot owner.
    if (input.isAdmin !== true && identity[2] !== actorId) {
      throw new AutoHealRunError("forbidden", "Only the source Bot owner or an admin may run this workflow from this source");
    }
    const lifecycleStage = resolveSourceStage(run.credentials_json);
    const userName = input.userName || actorId;
    // Rebuild the original trigger. A facade-triggered run records the command it
    // received in input_json; a cron- or dashboard-triggered run records only
    // params, so the workflow's facade binding supplies the command instead.
    const facadeRepo = this.deps.facadeRepo;
    const command = await resolveRerunCommand({
      inputJson: run.input_json,
      workflowId,
      lookupBoundCommand: async () => {
        const bindings = facadeRepo ? await facadeRepo.findByWorkflowId(workflowId) : [];
        return bindings[0]?.command ?? null;
      },
    });
    if (!command) {
      throw new AutoHealRunError("invalid_source",
        `源运行缺少可执行命令：input_json 无 command，且工作流 "${workflowId}" 未在 facade_bindings 中绑定命令`);
    }
    const message = `🚀 [自动修复-重新运行] ${userName}(${actorId}) 请求运行工作流\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📁 工作流: ${workflowId}\n⚡ 操作: 自动修复后重新运行\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n执行命令:\n${command}`;
    let receipt: Awaited<ReturnType<AutoHealRunSender>>;
    try {
      // New invocation, not the source cron/chat session. The service never retries delivery.
      receipt = await this.deps.sendIntervention({ botId, lifecycleStage,
        sessionKey: `auto-heal-run:${workflowId}:${randomUUID()}`, sessionId: null, message });
    } catch (error) {
      throw new AutoHealRunError("delivery_failed", error instanceof Error ? error.message : "BaaS intervention failed");
    }
    if (receipt?.ok !== true) {
      throw new AutoHealRunError(receipt?.tokenExpired ? "token_expired" : "delivery_failed",
        receipt?.error || "BaaS intervention failed");
    }
    if (typeof receipt.messageId !== "string" || !receipt.messageId.trim()) {
      throw new AutoHealRunError("delivery_failed", "BaaS did not confirm message acceptance (missing messageId)");
    }
    return { ok: true, workflowId, messageId: receipt.messageId,
      sessionId: typeof receipt.sessionId === "string" && receipt.sessionId.trim() ? receipt.sessionId : null };
  }
}
