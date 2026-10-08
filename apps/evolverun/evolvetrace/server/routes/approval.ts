/**
 * Approval API routes — card-web delivery endpoints.
 *
 * Shared approval endpoints for evolvetrace.  The approval_cards table is shared
 * between evolvetrace and clawweb (both point at the same database).
 *
 *   GET  /api/approval/by-flow/:flowId — list approval cards for a run
 *   GET  /api/approval/:id            — get approval details
 *   POST /api/approval/:id/resolve    — execute an approval action
 *   GET  /api/approval/:id/status     — poll approval status
 *   POST /api/approval/auth/dingtalk  — exchange DingTalk authCode (stub)
 */
import { Router, type Request, type Response } from "express";
import type { IDatabase } from "../db.js";
import { ApprovalCardRepository } from "../repositories/approval-card-repository.js";

/** Parse approval card content JSON into fields, sections, and display metadata. */
function parseApprovalCardContent(json: string | null | undefined): {
  fields: Array<{ label: string; value: string }>;
  sections?: unknown[];
  display?: Record<string, string>;
} {
  try {
    const value: unknown = JSON.parse(json ?? "null");
    const envelope =
      value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : undefined;
    const fields = Array.isArray(value)
      ? value
      : Array.isArray(envelope?.fields)
        ? envelope!.fields
        : [];
    const result: {
      fields: Array<{ label: string; value: string }>;
      sections?: unknown[];
      display?: Record<string, string>;
    } = {
      fields: fields
        .filter(
          (field: any) =>
            field &&
            typeof field.label === "string" &&
            ["string", "number", "boolean"].includes(typeof field.value),
        )
        .map((field: any) => ({ label: field.label, value: String(field.value) })),
    };
    if (Array.isArray(envelope?.sections)) {
      result.sections = envelope!.sections;
    }
    if (envelope?.display && typeof envelope.display === "object") {
      result.display = envelope.display as Record<string, string>;
    }
    return result;
  } catch {
    return { fields: [] };
  }
}

export function createApprovalRouter(db: IDatabase): Router {
  const router = Router();
  const repo = new ApprovalCardRepository(db);

  /** POST /auth/dingtalk — exchange DingTalk authCode for userId (stub) */
  router.post("/auth/dingtalk", (_req: Request, res: Response) => {
    // Community stub: no DingTalk OAuth backend configured.
    res.json({ ok: false, error: "社区版未配置钉钉认证" });
  });

  /** GET /by-flow/:flowId — list approval cards for a run */
  router.get("/by-flow/:flowId", async (req: Request, res: Response) => {
    try {
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
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[approval] by-flow failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** GET /:id — get approval details */
  router.get("/:id", async (req: Request, res: Response) => {
    try {
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
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[approval] get-by-id failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** POST /:id/resolve — execute approval action */
  router.post("/:id/resolve", async (req: Request, res: Response) => {
    try {
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

      const commentToStore =
        detail && typeof detail === "object" && Object.keys(detail).length > 0
          ? JSON.stringify({ note: comment ?? "", detail })
          : comment ?? undefined;

      const result = await repo.recordAction(id, empId, action as "approve" | "reject", commentToStore);

      if (!result.ok && result.error) {
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
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[approval] resolve failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** GET /:id/status — poll approval status */
  router.get("/:id/status", async (req: Request, res: Response) => {
    try {
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
        status: card.status,
        approvedBy: repo.parseList(card.approved_by),
        rejectedBy: repo.parseList(card.rejected_by),
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[approval] status failed: ${msg}`);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}
