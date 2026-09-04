import { Router, type Request, type Response } from "express";
import { asyncHandler } from "../middleware/async-handler.js";
import type {
  RepairApplyActionInput,
  RepairArtifactRefreshInput,
  RepairCfuseAuthCodeInput,
  RepairCfuseLoginInput,
  RepairCfuseLoginReportInput,
  RepairCreateTaskInput,
  RepairDecisionInput,
  RepairLogSearchInput,
  RepairOcbContextInput,
  RepairResumeInput,
  RepairRuntimeInspectInput,
  RepairSemanticConclusionInput,
  RepairTerminateInput,
  RepairWorkloadIdentity,
} from "../services/repair/contracts.js";
import { RepairError } from "../services/repair/errors.js";
import type { RepairTaskService } from "../services/repair/repair-runtime.js";
import {
  resolveRepairRequestActor,
  type VerifiedRequestActor,
} from "../services/repair/request-actor.js";
import type { RepairWorkloadVerifier } from "../services/repair/workload-verifier.js";

export type RepairRouterDeps = {
  service: RepairTaskService | null;
  workloadVerifier: RepairWorkloadVerifier | null;
  resolveActor?: (req: Request) => Promise<VerifiedRequestActor | null>;
};

function ocbAuthHeaders(req: Request, actor: VerifiedRequestActor): Record<string, string> {
  const headers: Record<string, string> = {};
  const cookie = req.header("cookie")?.trim();
  if (cookie) headers.cookie = cookie;
  headers["x-user-id"] = actor.userId;
  return headers;
}

function sendRepairError(res: Response, error: unknown): void {
  if (error instanceof RepairError) {
    res.status(error.status).json({
      error: error.code,
      message: error.message,
      ...(error.toolCallId ? { toolCallId: error.toolCallId } : {}),
      ...(error.recovery ? { recovery: error.recovery } : {}),
    });
    return;
  }
  throw error;
}

async function actorOrResponse(
  deps: RepairRouterDeps,
  req: Request,
  res: Response,
): Promise<VerifiedRequestActor | null> {
  const actor = await (deps.resolveActor ?? resolveRepairRequestActor)(req);
  if (actor) return actor;
  res.status(401).json({ error: "invalid_session", message: "登录态缺失或已失效" });
  return null;
}

async function workload(
  deps: RepairRouterDeps,
  req: Request,
  allowDecisionClaimAlias = false,
): Promise<RepairWorkloadIdentity> {
  if (!deps.workloadVerifier) {
    throw new RepairError(503, "repair_not_configured", "Repair workload verifier 当前不可用");
  }
  const identity = await deps.workloadVerifier.verify(req);
  const requestedStepId = String(req.params.stepId);
  const stepMatches = identity.stepId === requestedStepId
    || (allowDecisionClaimAlias && identity.requestedStepId === requestedStepId);
  if (identity.taskId !== String(req.params.taskId) || !stepMatches) {
    throw new RepairError(403, "repair_workload_route_mismatch", "工作负载身份与请求路径不匹配");
  }
  return identity;
}

export function createRepairRouter(deps: RepairRouterDeps): Router {
  const router = Router();
  const service = deps.service;
  if (!service) {
    router.use((_req, res) => res.status(503).json({
      error: "repair_not_configured",
      message: "Repair Task 当前不可用",
    }));
    return router;
  }

  router.get("/bots", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    res.json({
      userId: actor.userId,
      bots: await service.listBots(
        actor.userId,
        req.isClawEvolveAdmin === true,
        req.query.ownerId,
      ),
    });
  }));

  router.post("/tasks", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    try {
      const result = await service.createTask({
        actorUserId: actor.userId,
        authHeaders: ocbAuthHeaders(req, actor),
        body: req.body as RepairCreateTaskInput,
        ...(req.isClawEvolveAdmin === true ? { isAdmin: true } : {}),
      });
      res.status(202).json(result);
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.get("/tasks/:taskId", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    try {
      res.json(await service.getTask(
        actor.userId,
        String(req.params.taskId),
        req.isClawEvolveAdmin === true,
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.get("/tasks/:taskId/steps/:stepId/plan", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    res.set("Cache-Control", "private, no-store");
    try {
      res.json(await service.getStepPlan(
        actor.userId,
        String(req.params.taskId),
        String(req.params.stepId),
        req.isClawEvolveAdmin === true,
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.patch("/tasks/:taskId/share", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    if (typeof req.body?.shared !== "boolean") {
      res.status(400).json({ error: "invalid_repair_shared", message: "shared 必须为布尔值" });
      return;
    }
    try {
      res.json(await service.setTaskShared({
        actorUserId: actor.userId,
        isAdmin: req.isClawEvolveAdmin === true,
        taskId: String(req.params.taskId),
        shared: req.body.shared,
      }));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/tasks/:taskId/terminate", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    try {
      res.json(await service.terminateTask({
        actorUserId: actor.userId,
        taskId: String(req.params.taskId),
        reason: (req.body as RepairTerminateInput | undefined)?.reason,
        ...(req.isClawEvolveAdmin === true ? { isAdmin: true } : {}),
      }));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/tasks/:taskId/plan-decision", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    try {
      res.status(202).json(await service.decidePlan({
        actorUserId: actor.userId,
        authHeaders: ocbAuthHeaders(req, actor),
        taskId: String(req.params.taskId),
        body: req.body as RepairDecisionInput,
        ...(req.isClawEvolveAdmin === true ? { isAdmin: true } : {}),
      }));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/tasks/:taskId/result-decision", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    try {
      res.status(202).json(await service.decideResult({
        actorUserId: actor.userId,
        authHeaders: ocbAuthHeaders(req, actor),
        taskId: String(req.params.taskId),
        body: req.body as RepairDecisionInput,
        ...(req.isClawEvolveAdmin === true ? { isAdmin: true } : {}),
      }));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/tasks/:taskId/resume", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    try {
      res.status(202).json(await service.resumeTask({
        actorUserId: actor.userId,
        authHeaders: ocbAuthHeaders(req, actor),
        taskId: String(req.params.taskId),
        ...(req.isClawEvolveAdmin === true ? { isAdmin: true } : {}),
        body: {
          agentMode: (req.body as RepairResumeInput | undefined)?.agentMode,
          llmUseDefault: (req.body as RepairResumeInput | undefined)?.llmUseDefault,
          llmModel: (req.body as RepairResumeInput | undefined)?.llmModel,
          llmApiKey: (req.body as RepairResumeInput | undefined)?.llmApiKey,
          cfuseEngine: (req.body as RepairResumeInput | undefined)?.cfuseEngine,
          cfuseModel: (req.body as RepairResumeInput | undefined)?.cfuseModel,
        },
      }));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/tasks/:taskId/tool-calls/:toolCallId/cfuse-auth-code", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    try {
      res.set("Cache-Control", "no-store");
      res.status(202).json(await service.submitCfuseAuthCode({
        actorUserId: actor.userId,
        isAdmin: req.isClawEvolveAdmin === true,
        taskId: String(req.params.taskId),
        toolCallId: String(req.params.toolCallId),
        body: { authCode: (req.body as RepairCfuseAuthCodeInput | undefined)?.authCode },
      }));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/tasks/:taskId/tool-calls/:toolCallId/fulfill", asyncHandler(async (req, res) => {
    const actor = await actorOrResponse(deps, req, res);
    if (!actor) return;
    try {
      res.json(await service.fulfillToolCall({
        actorUserId: actor.userId,
        authHeaders: ocbAuthHeaders(req, actor),
        taskId: String(req.params.taskId),
        toolCallId: String(req.params.toolCallId),
        ...(req.isClawEvolveAdmin === true ? { isAdmin: true } : {}),
      }));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.get("/internal/tasks/:taskId/steps/:stepId/bootstrap", asyncHandler(async (req, res) => {
    try {
      res.json(await service.bootstrap(await workload(deps, req)));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/report", asyncHandler(async (req, res) => {
    try {
      res.json(await service.reportStep(await workload(deps, req), req.body));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/heartbeat", asyncHandler(async (req, res) => {
    try {
      res.json(await service.heartbeat(await workload(deps, req), req.body ?? {}));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/artifacts/refresh", asyncHandler(async (req, res) => {
    try {
      res.json(await service.refreshArtifactUpload(
        await workload(deps, req),
        req.body as RepairArtifactRefreshInput,
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/decision/claim", asyncHandler(async (req, res) => {
    try {
      res.json(await service.claimDecision(await workload(deps, req, true)));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/logs/search", asyncHandler(async (req, res) => {
    try {
      res.json(await service.searchLogs(await workload(deps, req), req.body as RepairLogSearchInput));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/runtime/inspect", asyncHandler(async (req, res) => {
    try {
      res.json(await service.inspectRuntime(await workload(deps, req), req.body as RepairRuntimeInspectInput));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/apply-action", asyncHandler(async (req, res) => {
    try {
      res.json(await service.applyAction(await workload(deps, req), req.body as RepairApplyActionInput));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/ocb", asyncHandler(async (req, res) => {
    try {
      res.status(202).json(await service.requestOcbOperation(
        await workload(deps, req),
        req.body as RepairOcbContextInput,
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/semantic-records", asyncHandler(async (req, res) => {
    try {
      res.json(await service.recordSemanticConclusion(
        await workload(deps, req),
        {
          sourceToolCallId: (req.body as RepairSemanticConclusionInput | undefined)?.sourceToolCallId,
          evidenceToolCallIds: (req.body as RepairSemanticConclusionInput | undefined)?.evidenceToolCallIds,
          conclusionZh: (req.body as RepairSemanticConclusionInput | undefined)?.conclusionZh,
          nextAction: (req.body as RepairSemanticConclusionInput | undefined)?.nextAction,
        },
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/semantic-records/system-closeout", asyncHandler(async (req, res) => {
    try {
      res.json(await service.systemCloseSemanticConclusion(
        await workload(deps, req),
        { sourceToolCallId: (req.body as { sourceToolCallId?: unknown } | undefined)?.sourceToolCallId },
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/cfuse-login", asyncHandler(async (req, res) => {
    try {
      res.status(202).json(await service.requestCfuseLogin(
        await workload(deps, req),
        {
          clientRequestId: (req.body as RepairCfuseLoginInput | undefined)?.clientRequestId,
          loginUrl: (req.body as RepairCfuseLoginInput | undefined)?.loginUrl,
        },
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/cfuse-login/:toolCallId/take", asyncHandler(async (req, res) => {
    try {
      res.set("Cache-Control", "no-store");
      res.json(await service.takeCfuseAuthCode(
        await workload(deps, req),
        String(req.params.toolCallId),
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/tools/cfuse-login/:toolCallId/report", asyncHandler(async (req, res) => {
    try {
      res.json(await service.reportCfuseLogin(
        await workload(deps, req),
        String(req.params.toolCallId),
        {
          status: (req.body as RepairCfuseLoginReportInput | undefined)?.status,
          errorCode: (req.body as RepairCfuseLoginReportInput | undefined)?.errorCode,
          errorMessage: (req.body as RepairCfuseLoginReportInput | undefined)?.errorMessage,
        },
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  router.get("/internal/tasks/:taskId/steps/:stepId/tools/calls/:toolCallId", asyncHandler(async (req, res) => {
    try {
      res.json(await service.getToolCall(
        await workload(deps, req),
        String(req.params.toolCallId),
      ));
    } catch (error) {
      sendRepairError(res, error);
    }
  }));

  return router;
}
