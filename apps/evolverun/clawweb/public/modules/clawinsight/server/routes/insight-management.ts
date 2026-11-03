import { InsightUnauthorizedError } from "../services/insight/insight-service.js";
import { Router, type Request, type RequestHandler } from "express";
import type { InsightAgentAuthorizer } from "../services/insight/agent-auth.js";
import { managementActions, ManagementError, type ManagementActor } from "../services/insight/management/contracts.js";
import type { InsightManagementService } from "../services/insight/management/service.js";

export function createInsightManagementRouter(service: InsightManagementService, authorizer: InsightAgentAuthorizer): Router {
  const router = Router();
  function actor(req: Request, machine: boolean): ManagementActor {
    if (machine) return { kind: "agent", id: authorizer.authorize(req, "improvement.manage") };
    if (!req.isAdmin) throw new ManagementError("FORBIDDEN", "需要管理员权限", 403);
    const id = req.get("X-Staff-Id") || req.get("staff_id") || req.get("X-User-Id") || req.cookies?.staff_id;
    if (typeof id !== "string" || !id.trim()) throw new ManagementError("UNAUTHORIZED", "无法识别操作人", 401);
    return { kind: "admin", id: id.trim() };
  }
  function guard(machine: boolean): RequestHandler {
    return (req, res, next) => {
      try {
        if (!machine && req.method !== "GET") {
          const origin = req.get("origin");
          if (req.get("X-Insight-Management-CSRF") !== "1" || req.get("sec-fetch-site") === "cross-site"
            || (origin && origin !== `${req.protocol}://${req.get("host")}`)) {
            throw new ManagementError("CSRF_REJECTED", "需要同源管理请求", 403);
          }
        }
        res.locals.managementActor = actor(req, machine); next();
      } catch (error) { next(error); }
    };
  }
  const idOf = (value: unknown) => {
    if (typeof value !== "string" || !/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(Number(value))) throw new ManagementError("INVALID_ARGUMENT", "improvementId 不合法", 400);
    return Number(value);
  };
  for (const [prefix, machine] of [["/admin/improvement-management", false], ["/internal/governance/improvement-management", true]] as const) {
    router.use((req, res, next) => {
      if (req.path === prefix || req.path.startsWith(`${prefix}/`)) return guard(machine)(req, res, next);
      next();
    });
    router.get(`${prefix}/capabilities`, (_req, res) => res.json({ contractVersion: "v1", actions: managementActions,
      requiresNewSession: false, requiresObservationWindow: false, startsOrCancelsRepair: false,
      grantsApproval: false, scope: "improvement.manage", maxBatchSize: 50 }));
    router.get(prefix, async (req, res) => {
      const input: Record<string, string> = {};
      for (const key of ["ownerUserId", "botId", "status", "cursor", "pageSize"]) {
        if (req.query[key] !== undefined) {
          if (typeof req.query[key] !== "string") throw new ManagementError("INVALID_ARGUMENT", `${key} 不合法`, 400);
          input[key] = req.query[key] as string;
        }
      }
      const pageSize = Number(input.pageSize ?? 50);
      if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) throw new ManagementError("INVALID_ARGUMENT", "pageSize 必须为1到100", 400);
      res.json(await service.list({ ...input, pageSize }));
    });
    router.get(`${prefix}/:id`, async (req, res) => res.json(await service.get(idOf(req.params.id))));
    router.post(`${prefix}/:id`, async (req, res) => res.json(await service.execute(idOf(req.params.id), req.body, res.locals.managementActor)));
    router.post(`${prefix}/batch/execute`, async (req, res) => {
      if (!req.body || Object.keys(req.body).some(key => key !== "items") || !Array.isArray(req.body.items)
        || req.body.items.length < 1 || req.body.items.length > 50) throw new ManagementError("INVALID_ARGUMENT", "items 必须包含1到50个操作", 400);
      const ids = new Set<number>();
      // Validate identities before performing any mutation; no implicit all-items scope.
      for (const item of req.body.items) {
        if (!item || typeof item !== "object" || !Number.isSafeInteger(item.improvementId) || item.improvementId < 1 || ids.has(item.improvementId)) {
          throw new ManagementError("INVALID_ARGUMENT", "批量ID必须明确且不重复", 400);
        }
        ids.add(item.improvementId);
      }
      const results = [];
      for (const { improvementId, ...body } of req.body.items) {
        try { results.push({ improvementId, ok: true, ...await service.execute(improvementId, body, res.locals.managementActor) }); }
        catch (error) {
          // Infrastructure errors abort the batch: a client must read back before retrying.
          if (!(error instanceof ManagementError)) throw error;
          results.push({ improvementId, ok: false, code: error.code, message: error.message });
        }
      }
      res.json({ results, atomic: false });
    });
  }
  router.use((error: unknown, _req: Request, res: import("express").Response, _next: import("express").NextFunction) => {
    if (error instanceof ManagementError) { res.status(error.status).json({ code: error.code, message: error.message }); return; }
    if (error instanceof InsightUnauthorizedError) { res.status(401).json({ code: "UNAUTHORIZED", message: error.message }); return; }
    res.status(500).json({ code: "MANAGEMENT_FAILED", message: "操作未确认，请先回读原请求结果再决定是否重试" });
  });
  return router;
}
