/**
 * GET /api/workflows/accessible
 *
 * Returns workflows visible to a given userId/botId combination.
 * Query modes:
 *   A: ?userId=u1          -> workflows visible to u1 across all bots
 *   B: ?botId=b1           -> workflows visible for b1 across all owners (admin-only)
 *   C: ?userId=u1&botId=b1 -> workflows visible to u1 on b1
 */
import { Router, type Request } from "express";
import { asyncHandler } from "../middleware/async-handler.js";
import type { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";

type AuthRequest = Request & { isAdmin?: boolean };

export function createAccessibleWorkflowsRouter(deps: {
  workflowSpecRepo: WorkflowSpecRepository | null;
}): Router {
  const router = Router();

  router.get("/", asyncHandler(async (req: AuthRequest, res) => {
    // ── Service availability ──
    if (!deps.workflowSpecRepo) {
      return res.status(503).json({
        error: "Service Unavailable",
        code: "SERVICE_UNAVAILABLE",
        message: "Database not configured",
      });
    }

    // ── Required params (at least one) ──
    const userIdRaw = String(req.query.userId ?? "").trim();
    const botIdRaw = String(req.query.botId ?? "").trim();
    const userId = userIdRaw || undefined;
    const botId = botIdRaw || undefined;

    if (!userId && !botId) {
      return res.status(400).json({
        error: "Bad Request",
        code: "INVALID_PARAMS",
        message: "at least one of [userId, botId] is required",
        details: { param: ["userId", "botId"], value: { userId: userIdRaw, botId: botIdRaw } },
      });
    }

    // ── Mode B (botId only) is admin-only ──
    if (!userId && botId && !req.isAdmin) {
      return res.status(403).json({
        error: "Forbidden",
        code: "FORBIDDEN",
        message: "botId-only mode requires admin",
      });
    }

    // ── Horizontal authorization: non-admin can only query themselves ──
    if (userId && !req.isAdmin) {
      const me = getRequestCookie(req, "staff_id") || req.headers["x-user-id"];
      if (me !== userId) {
        return res.status(403).json({
          error: "Forbidden",
          code: "FORBIDDEN",
          message: "Cannot query workflows for other users",
        });
      }
    }

    // ── Query ──
    let items, total;
    try {
      [items, total] = await Promise.all([
        deps.workflowSpecRepo.listAccessible({ userId, botId }),
        deps.workflowSpecRepo.countAccessible({ userId, botId }),
      ]);
    } catch (err) {
      console.error("[workflows-accessible] DB error:", err);
      return res.status(500).json({
        error: "Internal Server Error",
        code: "INTERNAL_ERROR",
        message: "Failed to load workflows",
      });
    }

    // ── Response ──
    res.json({
      items: items.map((row) => ({
        workflowId: row.workflow_id,
        title: row.title ?? "",
        packId: row.pack_id ?? row.workflow_id,
        updatedAt: toEpochMs(row.gmt_modified),
        command: row.workflow_id,
        ownerId: row.bot_owner_id,
        botId: row.bot_id ?? "",
      })),
      total,
    });
  }));

  return router;
}

function getRequestCookie(req: AuthRequest, name: string): string | undefined {
  const cookies = req.cookies as Record<string, string> | undefined;
  if (cookies?.[name]) return cookies[name];

  const rawCookie = req.get("cookie") ?? "";
  for (const part of rawCookie.split(";")) {
    const [key, ...valueParts] = part.trim().split("=");
    if (key === name) return valueParts.join("=");
  }
  return undefined;
}

function toEpochMs(value: Date | string | number | null | undefined): number {
  if (value == null) return 0;
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value > 1e12 ? value : value * 1000;
  const s = String(value).trim();
  if (!s) return 0;
  const ms = new Date(s.includes("T") ? s : s.replace(" ", "T") + "Z").getTime();
  return isNaN(ms) ? 0 : ms;
}
