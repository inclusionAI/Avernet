/**
 * Internal API routes for scheduled_triggers — full CRUD for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import type { ScheduledTriggerRepository } from "../../repositories/scheduled-trigger-repository.js";

type RouterDeps = { scheduledTriggerRepo: ScheduledTriggerRepository | null };
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

/**
 * Normalize and validate `params_json` coming from a request body.
 *
 * Accepts:
 *  - null/undefined/""  → null (no params)
 *  - string             → must parse as valid JSON; returned as-is (canonical)
 *  - object/array/number/boolean → JSON-stringified
 *
 * Returns { ok: true, value: string | null } on success, or
 * { ok: false, error: string } when a string is provided that is not valid JSON.
 *
 * This guard converts malformed params payloads (the root cause of the
 * "定时任务编辑更新提交参数格式错误导致 500" defect) into a clean 400 instead
 * of letting an opaque serialization error surface as a 500 deep in the
 * SQL driver.
 */
export function normalizeParamsJson(
  raw: unknown,
): { ok: true; value: string | null } | { ok: false; error: string } {
  if (raw === undefined || raw === null || raw === "") {
    return { ok: true, value: null };
  }
  // Object/array/primitive → serialize to a JSON string.
  if (typeof raw === "object" || typeof raw === "number" || typeof raw === "boolean") {
    try {
      return { ok: true, value: JSON.stringify(raw) };
    } catch {
      return { ok: false, error: "params_json object could not be serialized to JSON" };
    }
  }
  if (typeof raw !== "string") {
    return { ok: false, error: `params_json must be a string, object, or null; got ${typeof raw}` };
  }
  // String: must be valid JSON. Empty-ish strings → null.
  const trimmed = raw.trim();
  if (trimmed === "") return { ok: true, value: null };
  try {
    JSON.parse(trimmed);
    return { ok: true, value: trimmed };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return { ok: false, error: `params_json is not valid JSON: ${msg}` };
  }
}

export function createInternalScheduledTriggersRouter(repos: RouterDeps): Router {
  const router = Router();

  /** POST / — create a scheduled trigger */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.scheduledTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { trigger_id, workflow_id, pack_id, cron_expression, timezone, params_json, max_concurrent, enabled } = req.body as {
        trigger_id?: string;
        workflow_id?: string;
        pack_id?: string;
        cron_expression?: string;
        timezone?: string;
        params_json?: string;
        max_concurrent?: number;
        enabled?: number;
      };

      if (!trigger_id || !workflow_id || !pack_id || !cron_expression) {
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: trigger_id, workflow_id, pack_id, cron_expression" });
        return;
      }

      const normParams = normalizeParamsJson(params_json);
      if (!normParams.ok) {
        res.status(400).json({ success: false, error: "Bad Request", message: normParams.error });
        return;
      }

      const row = await repos.scheduledTriggerRepo.insert({
        trigger_id,
        workflow_id,
        pack_id,
        cron_expression,
        timezone,
        params_json: normParams.value,
        max_concurrent,
        enabled,
      });
      res.status(201).json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — list scheduled triggers */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.scheduledTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const enabled = req.query.enabled !== undefined ? parseInt(req.query.enabled as string, 10) : undefined;
      const limit = Math.min(parseInt(req.query.limit as string, 10) || 100, 500);
      const offset = parseInt(req.query.offset as string, 10) || 0;

      const rows = await repos.scheduledTriggerRepo.listAll({ enabled, limit, offset });
      res.json({ success: true, data: rows, total: rows.length, limit, offset });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /due — find triggers that are due to fire */
  router.get("/due", asyncHandler(async (_req: Request, res: Response) => {
    if (!repos.scheduledTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const rows = await repos.scheduledTriggerRepo.findDue();
      res.json({ success: true, data: rows });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:triggerId — get a scheduled trigger */
  router.get("/:triggerId", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.scheduledTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const row = await repos.scheduledTriggerRepo.findByTriggerId(String(req.params.triggerId));
      if (!row) {
        res.status(404).json({ success: false, error: "Not Found", message: `Scheduled trigger "${req.params.triggerId}" not found` });
        return;
      }
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /workflow/:workflowId — find by workflow id */
  router.get("/workflow/:workflowId", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.scheduledTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const rows = await repos.scheduledTriggerRepo.findByWorkflowId(String(req.params.workflowId));
      res.json({ success: true, data: rows });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:triggerId — update a scheduled trigger */
  router.put("/:triggerId", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.scheduledTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { cron_expression, timezone, params_json, max_concurrent, enabled, last_fire_time, next_fire_time } = req.body as {
        cron_expression?: string;
        timezone?: string;
        params_json?: string;
        max_concurrent?: number;
        enabled?: number;
        last_fire_time?: number;
        next_fire_time?: number;
      };

      // Normalize params_json defensively (front-end edit form may send an
      // object or a malformed string; without this guard the update throws
      // → 500). Treat invalid JSON as a 400.
      let normParamsValue: string | null | undefined;
      if (params_json !== undefined) {
        const normParams = normalizeParamsJson(params_json);
        if (!normParams.ok) {
          res.status(400).json({ success: false, error: "Bad Request", message: normParams.error });
          return;
        }
        normParamsValue = normParams.value;
      }

      const row = await repos.scheduledTriggerRepo.update(String(req.params.triggerId), {
        cronExpression: cron_expression,
        timezone,
        paramsJson: normParamsValue,
        maxConcurrent: max_concurrent,
        enabled: enabled !== undefined ? (enabled ? true : false) : undefined,
      });
      // Also update fire times if provided
      if (row && (last_fire_time !== undefined || next_fire_time !== undefined)) {
        await repos.scheduledTriggerRepo.updateFireTimes(String(req.params.triggerId), last_fire_time, next_fire_time);
      }
      if (!row) {
        res.status(404).json({ success: false, error: "Not Found", message: `Scheduled trigger "${req.params.triggerId}" not found` });
        return;
      }
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:triggerId — delete a scheduled trigger */
  router.delete("/:triggerId", asyncHandler(async (req: Request, res: Response) => {
    if (!repos.scheduledTriggerRepo) {
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const deleted = await repos.scheduledTriggerRepo.delete(String(req.params.triggerId));
      if (!deleted) {
        res.status(404).json({ success: false, error: "Not Found", message: `Scheduled trigger "${req.params.triggerId}" not found` });
        return;
      }
      res.json({ success: true, data: { affected: true } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}