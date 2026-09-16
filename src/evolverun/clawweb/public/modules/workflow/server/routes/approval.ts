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

// ── Route factory ──────────────────────────────────────────────────────

export function createApprovalRouter(db: IDatabase): Router {
  const router = Router();
  const repo = new ApprovalCardRepository(db);

  /**
   * GET /api/approval/:id — get approval details
   *
   * Query param: empId (optional) — if provided, checks if this person is an approver
   *
   * Returns the approval card data including card fields, approvers, and status.
   * empId is not required to view the card — it's only needed for action authorization.
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
   * Body: { empId: string, action: "approve" | "reject", comment?: string }
   *
   * 1. Verify empId is an authorized approver
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

    const { empId, action, comment, detail } = req.body as {
      empId?: string;
      action?: string;
      comment?: string;
      detail?: Record<string, unknown>;
    };

    if (!empId) {
      res.status(400).json({ error: "Bad Request", message: "缺少 empId" });
      return;
    }

    if (!action || (action !== "approve" && action !== "reject")) {
      res.status(400).json({ error: "Bad Request", message: "action 必须是 'approve' 或 'reject'" });
      return;
    }

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
   * Query param: empId (optional, for approver check)
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
