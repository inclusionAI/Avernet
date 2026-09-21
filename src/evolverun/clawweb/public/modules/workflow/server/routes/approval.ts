/**
 * Approval API routes — card-web delivery endpoints.
 *
 * Shared approval endpoints; the host supplies platform-specific identity exchange.
 *   GET  /api/approval/:id         — get approval details
 *   POST /api/approval/:id/resolve — execute an authorized approval action
 *   GET  /api/approval/:id/status  — poll approval status
 *
 * All data is read from/written to the approval_cards database table.
 * workflow runtime creates the row when sending the approval; clawweb serves the UI
 * and records the action; workflow runtime polls the DB to detect resolution.
 */

import { Router, type Request, type Response } from "express";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { ApprovalCardRepository } from "../repositories/approval-card-repository.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { parseApprovalCardContent } from "../../shared/approval-display.js";
import { invalidateResolvedCache } from "./internal/approval-cards.js";

export type DingTalkIdentityResult =
  | { ok: true; userId: string }
  | { ok: false; error: string };

export type ApprovalRouterOptions = {
  dingTalk?: {
    clientId: string;
    corpId: string;
    exchangeAuthCode: (authCode: string) => Promise<DingTalkIdentityResult>;
  };
};

// ── Route factory ──────────────────────────────────────────────────────

export function createApprovalRouter(db: IDatabase, options: ApprovalRouterOptions = {}): Router {
  const router = Router();
  const repo = new ApprovalCardRepository(db);

  router.get("/auth/dingtalk/config", (_req: Request, res: Response) => {
    const config = options.dingTalk;
    if (!config?.clientId || !config.corpId) {
      res.status(503).json({ error: "Service Unavailable", message: "钉钉免登未配置" });
      return;
    }
    res.json({ clientId: config.clientId, corpId: config.corpId });
  });

  /**
   * GET /api/approval/by-flow/:flowId — list approval cards for a run
   *
   * Returns all approval cards associated with the given flow_id (run ID),
   * newest first. Each card has the same shape as GET /api/approval/:id.
   */
  router.get("/by-flow/:flowId", asyncHandler(async (req: Request, res: Response) => {
    const flowId = String(req.params.flowId);
    if (!flowId) {
      res.status(400).json({ error: "Bad Request", message: "缺少 flowId" });
      return;
    }

    const cards = await repo.findByFlowId(flowId);
    const empId = String(req.query.empId ?? "");

    const items = cards.map((card) => {
      const isApprover = empId ? repo.isApprover(card, empId) : false;
      const approvedBy = repo.parseList(card.approved_by);
      const rejectedBy = repo.parseList(card.rejected_by);
      const approverIds = repo.parseList(card.approver_ids);
      const approverNames = card.approver_names
        ? card.approver_names.split(",").map((s) => s.trim())
        : approverIds;
      const { fields: cardFields, sections, display } = parseApprovalCardContent(card.card_fields_json);

      const item: Record<string, unknown> = {
        id: card.id,
        flowId: card.flow_id,
        nodeId: card.node_id,
        workflowId: card.workflow_id,
        workflowTitle: card.workflow_title,
        approvalType: card.approval_type,
        message: card.message,
        cardFields,
        ...(display ? { display } : {}),
        approverIds,
        approverNames,
        approvalPolicy: card.approval_policy,
        approvedBy,
        rejectedBy,
        status: card.status,
        deliveryMode: card.delivery_mode,
        createdAt: card.created_at,
        resolvedAt: card.resolved_at,
        isApprover,
      };
      if (sections) item.sections = sections;
      return item;
    });

    res.json({ items });
  }));

  /**
   * GET /api/approval/:id — get approval details
   *
   * Query param: empId (optional) — retained only as an untrusted display hint for old clients
   *
   * Returns the approval card data including card fields, approvers, and status.
   * empId is not required to view the card and never authorizes an action.
   *
   * card_fields_json supports two shapes:
   *   - Legacy: Array<{label, value}> → rendered as traditional card
   *   - Section mode: {fields: [...], sections: [...]} → rendered as interactive section card
   */
  router.get("/:id", asyncHandler(async (req: Request, res: Response) => {
    const id = parseInt(String(req.params.id), 10);
    if (Number.isNaN(id)) {
      res.status(400).json({ error: "Bad Request", message: "无效的审批 ID" });
      return;
    }

    const card = await repo.findById(id);
    if (!card) {
      res.status(404).json({ error: "Not Found", message: "审批记录未找到" });
      return;
    }

    const empId = String(req.query.empId ?? "");
    const isApprover = empId ? repo.isApprover(card, empId) : false;

    const approvedBy = repo.parseList(card.approved_by);
    const rejectedBy = repo.parseList(card.rejected_by);
    const approverIds = repo.parseList(card.approver_ids);
    const approverNames = card.approver_names
      ? card.approver_names.split(",").map((s) => s.trim())
      : approverIds;

    const { fields: cardFields, sections, display } = parseApprovalCardContent(card.card_fields_json);

    // Parse structured comment (detail + note) for resolved cards
    let comment: string | undefined = card.comment ?? undefined;
    let note: string | undefined;
    let detail: unknown | undefined;
    if (comment && comment.trim().startsWith("{")) {
      try {
        const parsed = JSON.parse(comment);
        if (parsed && typeof parsed === "object" && parsed.detail) {
          note = parsed.note ?? undefined;
          detail = parsed.detail;
        }
      } catch {
        // Not structured, keep raw comment
      }
    }

    const response: Record<string, unknown> = {
      id: card.id,
      flowId: card.flow_id,
      nodeId: card.node_id,
      workflowId: card.workflow_id,
      workflowTitle: card.workflow_title,
      approvalType: card.approval_type,
      message: card.message,
      cardFields,
      ...(display ? { display } : {}),
      approverIds,
      approverNames,
      approvalPolicy: card.approval_policy,
      approvedBy,
      rejectedBy,
      status: card.status,
      deliveryMode: card.delivery_mode,
      createdAt: card.created_at,
      resolvedAt: card.resolved_at,
      isApprover,
      empId: empId || undefined,
    };
    if (sections) response.sections = sections;
    if (comment) response.comment = comment;
    if (note) response.note = note;
    if (detail) response.detail = detail;

    res.json(response);
  }));

  /**
   * POST /api/approval/:id/resolve — execute approval action
   *
   * Body: { authCode: string, action: "approve" | "reject", comment?: string }
   *
   * 1. Exchange the short-lived authCode and verify the authenticated user is an approver
   * 2. Record the action in the DB
   * 3. Evaluate approval policy
   * 4. Update status if policy is met
   * No external callback — workflow runtime polls the DB.
   */
  router.post("/:id/resolve", asyncHandler(async (req: Request, res: Response) => {
    const id = parseInt(String(req.params.id), 10);
    if (Number.isNaN(id)) {
      res.status(400).json({ error: "Bad Request", message: "无效的审批 ID" });
      return;
    }

    const { authCode, action, comment, detail } = req.body as {
      authCode?: string;
      action?: string;
      comment?: string;
      detail?: Record<string, unknown>;
    };

    if (!authCode) {
      res.status(401).json({ error: "Unauthorized", message: "无法验证钉钉身份" });
      return;
    }

    if (!action || (action !== "approve" && action !== "reject")) {
      res.status(400).json({ error: "Bad Request", message: "action 必须是 'approve' 或 'reject'" });
      return;
    }

    if (!options.dingTalk) {
      res.status(503).json({ error: "Service Unavailable", message: "钉钉免登未配置" });
      return;
    }

    const identity = await options.dingTalk.exchangeAuthCode(authCode);
    if (identity.ok === false) {
      res.status(401).json({ error: "Unauthorized", message: identity.error });
      return;
    }
    const empId = identity.userId;

    // Build comment: if detail is provided, store as structured JSON; otherwise plain text
    const commentToStore = (detail && typeof detail === "object" && Object.keys(detail).length > 0)
      ? JSON.stringify({ note: comment ?? "", detail })
      : (comment ?? undefined);

    const result = await repo.recordAction(id, empId, action as "approve" | "reject", commentToStore);

    // Invalidate resolved-cards cache — status may have changed from pending to approved/rejected
    invalidateResolvedCache();

    if (!result.ok && result.error) {
      // Determine status code based on error type
      if (result.error === "审批记录未找到") {
        res.status(404).json({ error: "Not Found", message: result.error });
        return;
      }
      if (result.error === "您不是此审批的授权审批人") {
        res.status(403).json({ error: "Forbidden", message: result.error });
        return;
      }
      if (result.error === "审批已处理") {
        res.status(409).json({
          error: "Conflict",
          message: result.error,
          status: result.status,
          approvedBy: result.approvedBy,
          rejectedBy: result.rejectedBy,
        });
        return;
      }
      res.status(500).json({ error: "Internal Error", message: result.error });
      return;
    }

    res.json({
      ok: true,
      action,
      status: result.status,
      approvedBy: result.approvedBy,
      rejectedBy: result.rejectedBy,
      comment: comment ?? null,
    });
  }));

  /**
   * GET /api/approval/:id/status — poll approval status
   *
   * Lightweight endpoint for status polling (no card data, just status).
   */
  router.get("/:id/status", asyncHandler(async (req: Request, res: Response) => {
    const id = parseInt(String(req.params.id), 10);
    if (Number.isNaN(id)) {
      res.status(400).json({ error: "Bad Request", message: "无效的审批 ID" });
      return;
    }

    const card = await repo.findById(id);
    if (!card) {
      res.status(404).json({ error: "Not Found", message: "审批记录未找到" });
      return;
    }

    res.json({
      id: card.id,
      flowId: card.flow_id,
      nodeId: card.node_id,
      status: card.status,
      approvalPolicy: card.approval_policy,
      approvedBy: repo.parseList(card.approved_by),
      rejectedBy: repo.parseList(card.rejected_by),
      resolvedAt: card.resolved_at,
    });
  }));

  return router;
}
