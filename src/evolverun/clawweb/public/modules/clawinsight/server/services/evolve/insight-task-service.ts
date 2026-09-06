import { randomUUID } from "node:crypto";
import { getClawWebPublicBaseUrl } from "@avernet/clawweb-shared/server/env";
import type { EvolveStepRow, EvolveRepository, EvolveTaskRow } from "@avernet/clawevolve/server/repositories/evolve-repository";
import {
  ImprovementEvolveLinkConflictError,
  type InsightImprovementRepository,
} from "../../repositories/insight-improvement-repository.js";
import { parseNodeCommandYamls, resolveOpenClawExecutionMode, type NodeCommandYamls } from "@avernet/clawevolve/server/services/evolve/command";
import type { InsightPlanStepService } from "./insight-plan-step-service.js";
import type {
  AutoRepairGrantView,
  InsightAutoRepairRepository,
} from "../../repositories/insight-auto-repair-repository.js";
import type { GovernanceRuleProvider } from "../insight/governance-rule-provider.js";
import { readAutoRepairRule } from "../insight/auto-repair-policy.js";
import { defaultNodeCommand, taskNodeKeys } from "@avernet/clawevolve/server/services/evolve/task-registry";
import { verifyAdminConsentToken } from "../insight/admin-consent.js";
import {
  TaskSourceError,
  type TaskSourceService,
  type TaskSourceView,
} from "./task-source-service.js";

export type CreateInsightTaskInput = {
  taskType: unknown;
  taskName: unknown;
  remark: unknown;
  userId: unknown;
  botId: unknown;
  botEnv?: unknown;
  improvementId: unknown;
  crossBotConfirmed: unknown;
  maxRounds: unknown;
  nodeCommandYamls: unknown;
  forceMessage: unknown;
  runtimeMaintenance?: unknown;
  openclawExecutionMode?: unknown;
  idempotencyKey: string;
  actorUserId: string | null;
  persistAutoRepairGrant?: unknown;
  authorizationGrantId?: unknown;
  createdByOverride?: string;
  adminOverrideOnce?: {
    operatorUserId: string;
    reason: string;
    repairDirection?: string | null;
  };
  autoExecuteAfterConsent?: unknown;
  adminConsentToken?: unknown;
  callbackUrl: (taskId: string, stepId: string) => string;
};

export type InsightTaskCreationResult = {
  task: EvolveTaskRow;
  steps: EvolveStepRow[];
  source: TaskSourceView | null;
  idempotent: boolean;
  created: boolean;
};

export class InsightTaskCreationError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly category: "validation" | "auth" | "forbidden" | "not_found" | "conflict" | "source",
    readonly stage?: string,
    readonly retryable?: boolean,
  ) {
    super(message);
  }
}

function taskId(): string {
  const date = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  return `EV-${date}-${randomUUID().slice(0, 8).toUpperCase()}`;
}

function validation(message: string): never {
  throw new InsightTaskCreationError("INVALID_REQUEST", message, "validation");
}

export class InsightTaskService {
  constructor(
    private readonly repo: EvolveRepository,
    private readonly improvementRepo: InsightImprovementRepository,
    private readonly taskSourceService: TaskSourceService,
    private readonly insightPlanStepService: InsightPlanStepService,
    private readonly autoRepairRepo: InsightAutoRepairRepository | null = null,
    private readonly ruleProvider: GovernanceRuleProvider | null = null,
  ) {}

  async create(input: CreateInsightTaskInput): Promise<InsightTaskCreationResult> {
    const improvementIdRaw = String(input.improvementId ?? "").trim();
    const improvementId = Number(improvementIdRaw);
    const taskName = String(input.taskName ?? "").trim();
    const remark = String(input.remark ?? "").trim();
    const userId = String(input.userId ?? "").trim();
    const botId = String(input.botId ?? "").trim();
    const botEnv = String(input.botEnv ?? "").trim();
    const rounds = Number(input.maxRounds ?? 3);
    const requestId = input.idempotencyKey.trim();
    const authorizationGrantIdRaw = String(input.authorizationGrantId ?? "").trim();
    const authorizationGrantId = authorizationGrantIdRaw ? Number(authorizationGrantIdRaw) : null;
    const adminOverrideOnce = input.adminOverrideOnce ?? null;
    const adminConsentToken = String(input.adminConsentToken ?? "").trim();
    const autoRepairRepo = this.autoRepairRepo;
    const ruleProvider = this.ruleProvider;
    if (input.taskType !== "full") validation("Insight Improvement 当前只支持 taskType=full");
    if (!taskName || !userId || !botId) validation("taskName、userId、botId 为必填项");
    if (taskName.length > 128 || remark.length > 1000) {
      validation("任务名称不能超过128字，备注不能超过1000字");
    }
    if (!/^\d+$/.test(improvementIdRaw) || !Number.isSafeInteger(improvementId) || improvementId <= 0) {
      validation("input.improvementId 必须是正整数");
    }
    if (!requestId || requestId.length > 128) validation("Idempotency-Key 为必填项且不能超过128字");
    if (!Number.isSafeInteger(rounds) || rounds < 1 || rounds > 100) {
      validation("maxRounds 必须是 1 到 100 的整数");
    }
    if (
      authorizationGrantIdRaw
      && (!/^\d+$/.test(authorizationGrantIdRaw)
        || !Number.isSafeInteger(authorizationGrantId)
        || Number(authorizationGrantId) <= 0)
    ) {
      validation("authorizationGrantId 必须是正整数");
    }
    if (adminOverrideOnce) {
      if (!input.actorUserId || input.actorUserId !== adminOverrideOnce.operatorUserId) {
        throw new InsightTaskCreationError(
          "ADMIN_OVERRIDE_ACTOR_MISMATCH",
          "管理员代处理操作人与当前登录用户不一致",
          "forbidden",
        );
      }
      if (authorizationGrantId !== null || input.persistAutoRepairGrant === true) {
        throw new InsightTaskCreationError(
          "ADMIN_OVERRIDE_AUTH_CONFLICT",
          "管理员代处理不会创建或复用用户自动修复授权",
          "validation",
        );
      }
      if (!adminOverrideOnce.reason.trim()) validation("管理员代处理必须填写原因");
      if (adminOverrideOnce.reason.trim().length > 1000) validation("管理员代处理原因不能超过1000字");
      if ((adminOverrideOnce.repairDirection?.trim().length ?? 0) > 5000) {
        validation("管理员修复方向不能超过5000字");
      }
    }
    if (!input.actorUserId && authorizationGrantId === null) {
      throw new InsightTaskCreationError("AUTH_REQUIRED", "无法识别当前登录用户", "auth");
    }

    let authorizationGrant: AutoRepairGrantView | null = null;
    if (authorizationGrantId !== null) {
      if (!autoRepairRepo || !ruleProvider) {
        throw new InsightTaskCreationError(
          "AUTO_REPAIR_AUTH_UNAVAILABLE",
          "自动修复授权服务不可用",
          "source",
        );
      }
      authorizationGrant = await autoRepairRepo.findById(authorizationGrantId);
      if (!authorizationGrant || authorizationGrant.status !== "ACTIVE") {
        throw new InsightTaskCreationError(
          "AUTO_REPAIR_AUTH_INVALID",
          "自动修复授权不存在或已撤销",
          "forbidden",
        );
      }
    }

    const improvement = authorizationGrant || adminOverrideOnce || input.autoExecuteAfterConsent === true
      ? await this.improvementRepo.getDetailById(improvementId)
      : await this.improvementRepo.getDetail(input.actorUserId ?? "", improvementId);
    if (!improvement) {
      throw new InsightTaskCreationError("IMPROVEMENT_NOT_FOUND", "改进项不存在", "not_found");
    }
    if (authorizationGrant) {
      if (!autoRepairRepo || !ruleProvider) {
        throw new InsightTaskCreationError(
          "AUTO_REPAIR_AUTH_UNAVAILABLE",
          "自动修复授权服务不可用",
          "source",
        );
      }
      if (
        authorizationGrant.ownerUserId !== userId
        || authorizationGrant.botId !== botId
        || improvement.actionType !== "DIRECT_EVOLUTION"
        || !improvement.sourceRuleId
      ) {
        throw new InsightTaskCreationError(
          "AUTO_REPAIR_AUTH_SCOPE_MISMATCH",
          "自动修复授权与当前用户、Bot 或治理规则不匹配",
          "forbidden",
        );
      }
      const rule = await readAutoRepairRule(
        ruleProvider,
        improvement.sourceRuleId,
        "DIRECT_EVOLUTION",
      );
      const currentGrant = rule
        ? await autoRepairRepo.findActiveGrant(userId, botId, rule)
        : null;
      if (!rule || currentGrant?.grantId !== authorizationGrant.grantId) {
        throw new InsightTaskCreationError(
          "AUTO_REPAIR_AUTH_SCOPE_CHANGED",
          "治理规则版本、允许修改范围或风险等级已变化，需要重新授权",
          "forbidden",
        );
      }
    } else if (!adminOverrideOnce && userId !== input.actorUserId) {
      throw new InsightTaskCreationError(
        "TARGET_USER_FORBIDDEN",
        "Insight Improvement 只能在当前处理用户的用户空间执行",
        "forbidden",
      );
    }
    if (adminOverrideOnce && improvement.adminReviewStatus === "PENDING") {
      throw new InsightTaskCreationError(
        "ADMIN_REVIEW_REQUIRED",
        "候选改进项必须先批准后，才能由管理员代用户执行",
        "conflict",
      );
    }
    const targetRuntime = await this.repo.resolveEvolveBotRuntime(userId, botId, botEnv);
    if (!targetRuntime) {
      throw new InsightTaskCreationError(
        "TARGET_BOT_FORBIDDEN",
        "目标 Bot 不存在或当前用户无权操作",
        "forbidden",
      );
    }
    if (
      targetRuntime.botType?.toLowerCase() === "service"
      && (improvement.actionType === "DIRECT_EVOLUTION" || adminOverrideOnce)
    ) {
      throw new InsightTaskCreationError(
        "AUTO_REPAIR_SERVICE_BOT_FORBIDDEN",
        adminOverrideOnce
          ? "管理员代处理只能在测试 Bot 上执行，不会修改服务 Bot"
          : "自动修复只能在测试 Bot 上执行，不会修改服务 Bot",
        "forbidden",
      );
    }
    if (targetRuntime.activeEngine && targetRuntime.activeEngine.toLowerCase() !== "openclaw") {
      throw new InsightTaskCreationError(
        "EVOLVE_ENGINE_UNSUPPORTED",
        `当前进化流程仅支持 OpenClaw 引擎，所选 Bot 为 ${targetRuntime.activeEngine}`,
        "source",
      );
    }
    const crossBot = userId !== improvement.botOwnerUserId || botId !== improvement.botId;
    if (crossBot && input.crossBotConfirmed !== true) {
      throw new InsightTaskCreationError(
        "CROSS_BOT_CONFIRMATION_REQUIRED",
        "跨 Bot 执行必须明确确认 Evidence 来源与实际执行目标不同",
        "validation",
      );
    }
    const existing = await this.existingResult(improvementId, requestId, userId, botId);
    if (existing) return existing;

    if (input.persistAutoRepairGrant === true) {
      if (!autoRepairRepo || !ruleProvider) {
        throw new InsightTaskCreationError(
          "AUTO_REPAIR_AUTH_UNAVAILABLE",
          "自动修复授权服务不可用",
          "source",
        );
      }
      if (improvement.actionType !== "DIRECT_EVOLUTION" || !improvement.sourceRuleId) {
        throw new InsightTaskCreationError(
          "AUTO_REPAIR_AUTH_NOT_ALLOWED",
          "当前改进项不是可持久授权的自动修复项",
          "validation",
        );
      }
      const rule = await readAutoRepairRule(
        ruleProvider,
        improvement.sourceRuleId,
        "DIRECT_EVOLUTION",
      );
      if (!rule) {
        throw new InsightTaskCreationError(
          "AUTO_REPAIR_RULE_CHANGED",
          "治理规则不存在、已停用或动作类型已变化，需要刷新后重新确认",
          "source",
        );
      }
      authorizationGrant = await autoRepairRepo.grant({
        ownerUserId: userId,
        botId,
        rule,
        sourceImprovementId: improvement.improvementId,
        grantedBy: input.actorUserId ?? userId,
        autoExecute: input.autoExecuteAfterConsent === true,
      });
    }

    if (input.autoExecuteAfterConsent === true) {
      if (!authorizationGrant || !adminConsentToken) {
        throw new InsightTaskCreationError("ADMIN_CONSENT_REQUIRED", "持续自动修复必须通过管理员发出的 Owner 授权链接确认", "forbidden");
      }
      try {
        verifyAdminConsentToken(adminConsentToken, {
          improvementId,
          ownerUserId: userId,
          botId,
          sourceRuleId: authorizationGrant.sourceRuleId,
          ruleVersion: authorizationGrant.ruleVersion,
        });
      } catch (error) {
        throw new InsightTaskCreationError("ADMIN_CONSENT_INVALID", error instanceof Error ? error.message : String(error), "forbidden");
      }
    }

    let nodeCommands: NodeCommandYamls;
    try {
      nodeCommands = parseNodeCommandYamls(input.nodeCommandYamls, [...taskNodeKeys("full", "insight_improvement")]);
    } catch (error) {
      validation(error instanceof Error ? error.message : String(error));
    }
    const dispatchMode = targetRuntime.botType?.toLowerCase() === "service" ? "run" : "message";
    const autoRepairConsentMode = adminOverrideOnce
      ? "ADMIN_ONCE"
      : authorizationGrantId !== null || input.persistAutoRepairGrant === true
        ? "PERSISTENT"
        : "ONCE";
    const newTaskId = taskId();
    const config = {
      input: { type: "insight_improvement", improvementId },
      maxRounds: rounds,
      nodeCommands: {
        plan: nodeCommands.plan ?? defaultNodeCommand("plan"),
        optimize: nodeCommands.optimize ?? defaultNodeCommand("optimize"),
      },
      dispatchMode,
      botEnv,
      clawwebUrl: getClawWebPublicBaseUrl(),
      forceMessage: input.forceMessage === true,
      runtimeMaintenance: input.runtimeMaintenance !== false,
      openclawExecutionMode: resolveOpenClawExecutionMode(input.openclawExecutionMode),
      ...(improvement.actionType === "DIRECT_EVOLUTION" ? {
        autoRepairAuthorization: {
          consentMode: autoRepairConsentMode,
          ...(authorizationGrant ? {
            grantId: authorizationGrant.grantId,
            mode: input.authorizationGrantId ? "PERSISTED_GRANT" : "OWNER_CONSENT",
          } : {}),
        },
      } : {}),
      ...(adminOverrideOnce ? {
        adminOverride: {
          mode: "ADMIN_ONCE",
          operatorUserId: adminOverrideOnce.operatorUserId,
          targetUserId: userId,
          targetBotId: botId,
          reason: adminOverrideOnce.reason.trim(),
          repairDirection: adminOverrideOnce.repairDirection?.trim() || null,
          persistentAuthorization: false,
        },
      } : {}),
      ...(dispatchMode === "run" ? { lifecycleStage: "draft" } : {}),
    };
    const createdBy = adminOverrideOnce
      ? (input.createdByOverride?.trim() || "insight-admin-override")
      : authorizationGrantId !== null
      ? (input.createdByOverride?.trim() || "insight-auto-repair")
      : (input.actorUserId ?? userId);
    await this.repo.createTask({
      taskId: newTaskId,
      taskType: "full",
      userId,
      botId,
      taskName,
      remark: remark || null,
      configJson: JSON.stringify(config),
      createdBy,
    });
    try {
      await this.taskSourceService.freezeInsight(
        newTaskId,
        improvement,
        {
          ownerUserId: userId,
          botId,
          selectedBy: input.actorUserId ?? createdBy,
          crossBotConfirmed: crossBot,
        },
        adminOverrideOnce
          ? {
              mode: "ADMIN_ONCE",
              operatorUserId: adminOverrideOnce.operatorUserId,
              reason: adminOverrideOnce.reason.trim(),
              repairDirection: adminOverrideOnce.repairDirection?.trim() || null,
            }
          : undefined,
      );
      await this.improvementRepo.linkEvolveTask({
        improvementId,
        ownerUserId: improvement.ownerUserId,
        evolveTaskId: newTaskId,
        requestId,
        createdBy,
      });
    } catch (error) {
      await this.repo.deleteTask(newTaskId);
      if (error instanceof ImprovementEvolveLinkConflictError) {
        const raced = await this.existingResult(improvementId, requestId, userId, botId);
        if (raced) return raced;
        throw new InsightTaskCreationError(error.code, error.message, "conflict");
      }
      if (error instanceof TaskSourceError) {
        throw new InsightTaskCreationError(
          error.code,
          error.message,
          "source",
          error.stage,
          error.retryable,
        );
      }
      throw error;
    }

    const task = await this.repo.findTask(newTaskId);
    if (!task) {
      throw new InsightTaskCreationError("TASK_STATE_CONFLICT", "Task 创建后不可见", "conflict");
    }
    await this.insightPlanStepService.start(task, 0, (stepId) => input.callbackUrl(newTaskId, stepId));
    return this.result(newTaskId, false, true);
  }

  private async existingResult(
    improvementId: number,
    requestId: string,
    expectedUserId: string,
    expectedBotId: string,
  ): Promise<InsightTaskCreationResult | null> {
    const link = await this.improvementRepo.findEvolveLinkByRequest(improvementId, requestId);
    if (!link) return null;
    const task = await this.repo.findTask(link.evolve_task_id);
    if (!task) {
      throw new InsightTaskCreationError(
        "TASK_STATE_CONFLICT",
        "改进项已关联 Evolve Task，但任务记录不存在",
        "conflict",
      );
    }
    if (task.user_id !== expectedUserId || task.bot_id !== expectedBotId) {
      throw new InsightTaskCreationError(
        "IDEMPOTENCY_TARGET_CONFLICT",
        "同一 Idempotency-Key 已绑定到不同的 Evolve 执行目标",
        "conflict",
      );
    }
    return this.result(task.task_id, true, false);
  }

  private async result(
    taskIdValue: string,
    idempotent: boolean,
    created: boolean,
  ): Promise<InsightTaskCreationResult> {
    const task = await this.repo.findTask(taskIdValue);
    if (!task) {
      throw new InsightTaskCreationError("TASK_STATE_CONFLICT", "Evolve Task 不存在", "conflict");
    }
    const [steps, source] = await Promise.all([
      this.repo.listSteps(taskIdValue),
      this.taskSourceService.findView(taskIdValue),
    ]);
    return { task, steps, source, idempotent, created };
  }
}
