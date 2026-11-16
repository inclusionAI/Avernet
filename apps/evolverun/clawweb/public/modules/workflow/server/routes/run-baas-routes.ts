/** BaaS-backed run actions. Run reads and local control remain in their existing routers. */
import { Router, type Request, type Response } from "express";
import type { FlowRunRepository } from "../repositories/flow-run-repository.js";
import type { FlowEventRepository } from "../repositories/event-repository.js";
import type { FacadeBindingRepository } from "../repositories/facade-binding-repository.js";
import { sendIntervention } from "@avernet/clawevolve/server/services/baas-intervention";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { buildInterventionMessage, type InterventionAction } from "../services/intervention-message-builder.js";
import { resolveRerunCommand } from "../services/rerun-command.js";

/** Flow statuses that allow human intervention */
const INTERVENABLE_STATUSES = new Set(["failed", "blocked", "waiting"]);

/** Valid intervention actions */
const VALID_ACTIONS = new Set<InterventionAction>(["retry", "skip", "revise", "confirm"]);

/**
 * Compute which intervention actions are available for a flow run
 * based on its status and the credentials/session info it carries.
 */
export function computeAvailableInterventions(run: {
  status: string;
  origin_bot_id: string | null;
  origin_session_key: string | null;
  credentials_json: string | null;
}): InterventionAction[] {
  if (!INTERVENABLE_STATUSES.has(run.status)) return [];
  // Must have BaaS routing info to send intervention messages
  if (!run.origin_bot_id || !run.origin_session_key) return [];

  const actions: InterventionAction[] = [];
  if (run.status === "failed" || run.status === "blocked") {
    actions.push("retry", "skip", "revise");
  }
  if (run.status === "waiting") {
    actions.push("confirm", "skip");
  }
  return actions;
}

export function createRunBaasRouter(
  flowRunRepo: FlowRunRepository | null,
  eventRepo: FlowEventRepository | null,
  effectiveFacadeRepo: FacadeBindingRepository | null,
): Router {
  const router = Router();
  /** GET /:flowId/interventions — query available intervention actions for a flow */
  router.get("/:flowId/interventions", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const run = await flowRunRepo.findFullByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      const availableInterventions = computeAvailableInterventions(run);
      const canIntervene = availableInterventions.length > 0;

      res.json({
        flowId,
        status: run.status,
        canIntervene,
        availableInterventions,
        interventionReady: canIntervene && !!run.origin_bot_id && !!run.origin_session_key,
        originBotId: run.origin_bot_id,
        originSessionKey: run.origin_session_key,
        originSessionId: run.origin_session_id,
        hasCredentials: run.credentials_json != null && run.credentials_json !== "",
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** PATCH /:flowId/session — update BaaS session info for a flow run */
  router.patch("/:flowId/session", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const { originBotId, originSessionKey, originSessionId } = req.body as {
        originBotId?: string | null;
        originSessionKey?: string | null;
        originSessionId?: string | null;
      };

      // At least one field must be provided
      if (originBotId === undefined && originSessionKey === undefined && originSessionId === undefined) {
        res.status(400).json({
          error: "Bad Request",
          message: "至少需要提供 originBotId、originSessionKey 或 originSessionId 之一",
        });
        return;
      }

      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      const updated = await flowRunRepo.updateSessionInfo(flowId, {
        originBotId,
        originSessionKey,
        originSessionId,
      });

      if (!updated) {
        res.status(500).json({ error: "Internal Server Error", message: "Failed to update session info" });
        return;
      }

      // Return updated session info (never expose credentials_json)
      const refreshed = await flowRunRepo.findByFlowId(flowId);
      res.json({
        ok: true,
        flowId,
        originBotId: refreshed?.origin_bot_id ?? null,
        originSessionKey: refreshed?.origin_session_key ?? null,
        originSessionId: refreshed?.origin_session_id ?? null,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:flowId/intervene — send human intervention message via BaaS */
  router.post("/:flowId/intervene", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const { action, nodeId, nodeTitle, reason } = req.body as {
        action?: string;
        nodeId?: string;
        nodeTitle?: string;
        reason?: string;
      };

      // Validate action
      if (!action || !VALID_ACTIONS.has(action as InterventionAction)) {
        res.status(400).json({
          error: "Bad Request",
          message: `Invalid action "${action}". Must be one of: ${[...VALID_ACTIONS].join(", ")}`,
        });
        return;
      }

      const typedAction = action as InterventionAction;

      // Load flow run
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Check if flow is in an intervenable status
      if (!INTERVENABLE_STATUSES.has(run.status)) {
        res.status(409).json({
          error: "Conflict",
          message: `流程状态为 "${run.status}"，无法干预。仅支持状态: ${[...INTERVENABLE_STATUSES].join(", ")}`,
        });
        return;
      }

      // Check if BaaS routing info is available
      if (!run.origin_bot_id || !run.origin_session_key) {
        res.status(422).json({
          error: "Unprocessable Entity",
          message: "该流程实例缺少 BaaS 会话信息 (origin_bot_id / origin_session_key)，无法发送干预消息",
          hint: "流程可能不是从 BaaS 会话启动的，或版本过旧未记录会话信息",
        });
        return;
      }

      // Check if action is valid for this status
      const available = computeAvailableInterventions(run);
      if (!available.includes(typedAction)) {
        res.status(409).json({
          error: "Conflict",
          message: `操作 "${typedAction}" 在当前状态 "${run.status}" 下不可用。可用操作: ${available.join(", ") || "无"}`,
          availableInterventions: available,
        });
        return;
      }

      // Get operator identity from headers
      const operatorId = (req.headers["x-user-id"] as string | undefined)?.trim() || "unknown";
      const operatorName = (req.headers["x-user-name"] as string | undefined)?.trim() || operatorId;

      // Build intervention message
      const message = buildInterventionMessage({
        action: typedAction,
        flowId,
        workflowTitle: run.workflow_title ?? undefined,
        nodeId,
        nodeTitle,
        reason,
        operatorId,
        operatorName,
      });

      // Send via BaaS
      const result = await sendIntervention({
        botId: run.origin_bot_id,
        sessionKey: run.origin_session_key,
        sessionId: run.origin_session_id,
        message,
      });

      if (!result.ok) {
        const statusCode = result.tokenExpired ? 401 : 502;
        res.status(statusCode).json({
          error: result.tokenExpired ? "Unauthorized" : "Bad Gateway",
          message: result.error,
          tokenExpired: result.tokenExpired,
        });
        return;
      }

      // Persist BaaS session_id so subsequent interventions use the same session (multi-turn)
      if (result.sessionId && result.sessionId !== run.origin_session_id) {
        try {
          await flowRunRepo.updateSessionInfo(flowId, {
            originSessionId: result.sessionId,
          });
        } catch (updateErr) {
          console.warn(`[runs] failed to persist session_id for ${flowId}:`, updateErr);
        }
      }

      // Record intervention event for audit trail
      if (eventRepo) {
        try {
          await eventRepo.insert({
            id: `intervention-${flowId}-${Date.now()}`,
            time: Math.floor(Date.now() / 1000),
            type: "human_intervention",
            flowId,
            workflowId: run.workflow_id,
            nodeId: nodeId ?? null,
            data: {
              action: typedAction,
              operatorId,
              operatorName,
              reason: reason ?? null,
              baasMessageId: result.messageId,
              baasSessionId: result.sessionId,
            },
          });
        } catch (eventErr) {
          // Audit logging is best-effort, don't fail the intervention
          console.warn(`[runs] failed to record intervention event for ${flowId}:`, eventErr);
        }
      }

      res.json({
        ok: true,
        flowId,
        action: typedAction,
        messageId: result.messageId,
        sessionId: result.sessionId,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:flowId/rerun — re-trigger a workflow by resending the original command to origin_bot_id */
  router.post("/:flowId/rerun", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);

      // Load flow run (full row including input_json)
      const run = await flowRunRepo.findFullByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Validate origin_bot_id — must exist to send the command
      if (!run.origin_bot_id) {
        res.status(422).json({
          error: "Unprocessable Entity",
          message: "该流程实例缺少 origin_bot_id，无法重跑。流程可能不是从 BaaS 会话启动的。",
        });
        return;
      }

      // Rebuild the original trigger. A facade-triggered run records the command it
      // received in input_json; a cron-, dashboard- or gateway-triggered run records
      // only params, so the workflow's facade binding supplies the command instead.
      // Without that lookup every scheduled workflow was unrerunnable.
      const command = await resolveRerunCommand({
        inputJson: run.input_json,
        workflowId: run.workflow_id,
        markers: { env: run.env, tag: run.tag },
        lookupBoundCommand: async () => {
          const bindings = effectiveFacadeRepo ? await effectiveFacadeRepo.findByWorkflowId(run.workflow_id) : [];
          return bindings[0]?.command ?? null;
        },
      });

      if (!command) {
        res.status(422).json({
          error: "Unprocessable Entity",
          message: `该流程实例缺少可执行命令：input_json 无 command，且工作流 "${run.workflow_id}" 未在 facade_bindings 中绑定命令，无法重跑。`,
        });
        return;
      }

      // Get operator identity
      const operatorId = (req.headers["x-user-id"] as string | undefined)?.trim() || "unknown";
      const operatorName = (req.headers["x-user-name"] as string | undefined)?.trim() || operatorId;

      // Build BaaS message — send the original command to the original bot
      const message = `🔄 [重跑] ${operatorName}(${operatorId}) 重新触发工作流\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n📁 工作流: ${run.workflow_id}${run.workflow_title ? ` (${run.workflow_title})` : ""}\n📎 原始运行: ${flowId}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n执行命令:\n${command}`;

      // Extract lifecycle_stage from credentials_json (STAGE field) so rerun
      // targets the same bot lifecycle stage (draft / verify / online) as the
      // original run. Without this, BaaS defaults to "online" and may fail with
      // "Bot binding not found" for draft/verify bots.
      let lifecycleStage: "draft" | "verify" | "online" | undefined;
      if (run.credentials_json) {
        try {
          const creds = JSON.parse(run.credentials_json) as Record<string, unknown>;
          const stage = (creds.STAGE as string | undefined)?.trim();
          if (stage === "draft" || stage === "verify" || stage === "online") {
            lifecycleStage = stage;
          }
        } catch {
          // credentials_json is not valid JSON — fall through, keep undefined
        }
      }

      // Send via BaaS — use a fresh session key since this is a new invocation
      const result = await sendIntervention({
        botId: run.origin_bot_id,
        sessionKey: `rerun:${run.workflow_id}:${Date.now()}`,
        sessionId: null,
        message,
        lifecycleStage,
      });

      if (!result.ok) {
        const statusCode = result.tokenExpired ? 401 : 502;
        res.status(statusCode).json({
          error: result.tokenExpired ? "Unauthorized" : "Bad Gateway",
          message: `重跑发送失败: ${result.error ?? "unknown error"}`,
          tokenExpired: result.tokenExpired,
        });
        return;
      }

      // Record rerun event for audit trail
      if (eventRepo) {
        try {
          await eventRepo.insert({
            id: `rerun-${flowId}-${Date.now()}`,
            time: Math.floor(Date.now() / 1000),
            type: "rerun_triggered",
            flowId,
            workflowId: run.workflow_id,
            nodeId: null,
            data: {
              action: "rerun",
              operatorId,
              operatorName,
              originalFlowId: flowId,
              command,
              originBotId: run.origin_bot_id,
              lifecycleStage: lifecycleStage ?? null,
              baasMessageId: result.messageId,
              baasSessionId: result.sessionId,
            },
          });
        } catch (eventErr) {
          console.warn(`[runs] failed to record rerun event for ${flowId}:`, eventErr);
        }
      }

      res.json({
        ok: true,
        flowId,
        newFlowId: result.messageId ?? null,
        sessionId: result.sessionId ?? null,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /:flowId/chat — send a free-text chat message via BaaS (multi-turn conversation) */
  router.post("/:flowId/chat", asyncHandler(async (req: Request, res: Response) => {
    if (!flowRunRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const { message } = req.body as { message?: string };

      if (!message || !message.trim()) {
        res.status(400).json({ error: "Bad Request", message: "消息内容不能为空" });
        return;
      }

      // Load flow run
      const run = await flowRunRepo.findByFlowId(flowId);
      if (!run) {
        res.status(404).json({ error: "Not Found", message: `Flow ${flowId} not found` });
        return;
      }

      // Check BaaS routing info
      if (!run.origin_bot_id || !run.origin_session_key) {
        res.status(422).json({
          error: "Unprocessable Entity",
          message: "该流程实例缺少 BaaS 会话信息 (origin_bot_id / origin_session_key)，无法发送消息",
        });
        return;
      }

      // Get operator identity
      const operatorId = (req.headers["x-user-id"] as string | undefined)?.trim() || "unknown";

      // Send via BaaS — no action validation, just pass the message through
      const result = await sendIntervention({
        botId: run.origin_bot_id,
        sessionKey: run.origin_session_key,
        sessionId: run.origin_session_id,
        message: message.trim(),
      });

      if (!result.ok) {
        const statusCode = result.tokenExpired ? 401 : 502;
        res.status(statusCode).json({
          error: result.tokenExpired ? "Unauthorized" : "Bad Gateway",
          message: result.error,
          tokenExpired: result.tokenExpired,
        });
        return;
      }

      // Persist BaaS session_id so subsequent chat messages use the same session (multi-turn)
      // First call: origin_session_id is null → BaaS creates a new session and returns session_id.
      // Subsequent calls: origin_session_id is set → BaaS routes to the same session.
      if (result.sessionId && result.sessionId !== run.origin_session_id) {
        try {
          await flowRunRepo.updateSessionInfo(flowId, {
            originSessionId: result.sessionId,
          });
        } catch (updateErr) {
          console.warn(`[runs] failed to persist session_id for ${flowId}:`, updateErr);
        }
      }

      // Record chat event for audit trail
      if (eventRepo) {
        try {
          await eventRepo.insert({
            id: `chat-${flowId}-${Date.now()}`,
            time: Math.floor(Date.now() / 1000),
            type: "human_intervention",
            flowId,
            workflowId: run.workflow_id,
            nodeId: null,
            data: {
              action: "chat",
              operatorId,
              message: message.trim().substring(0, 500),
              baasMessageId: result.messageId,
              baasSessionId: result.sessionId,
            },
          });
        } catch (eventErr) {
          console.warn(`[runs] failed to record chat event for ${flowId}:`, eventErr);
        }
      }

      res.json({
        ok: true,
        flowId,
        messageId: result.messageId,
        sessionId: result.sessionId,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}
