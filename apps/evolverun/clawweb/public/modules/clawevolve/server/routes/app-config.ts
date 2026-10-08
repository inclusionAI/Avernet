import { Router } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { AppConfigRepository, AppConfigRow } from "../repositories/app-config-repository.js";

/** Generic JSON configuration; the host owns authentication and administrator selection. */
export function createAppConfigRouter(repo: AppConfigRepository): Router {
  const router = Router();
  // Protect reads as well as writes, including when no host auth middleware is installed.
  router.use((req, res, next) => {
    if (req.isAdmin !== true) {
      res.status(403).json({ error: "Admin permission required" });
      return;
    }
    next();
  });
  router.param("configKey", (_req, res, next, key: string) => {
    if (!validKey(key)) { res.status(400).json({ error: "config_key must contain 1–64 non-padding characters" }); return; }
    next();
  });

  router.get("/", asyncHandler(async (req, res) => {
    const rows = await repo.listAll(req.query.enabled === "true");
    res.json(rows.map(rowToApi));
  }));
  router.get("/:configKey", asyncHandler(async (req, res) => {
    const row = await repo.findByKey(String(req.params.configKey));
    if (!row) { res.status(404).json({ error: "Config not found" }); return; }
    res.json(rowToApi(row));
  }));
  router.post("/", asyncHandler(async (req, res) => {
    const error = validateBody(req.body, true);
    if (error) { res.status(400).json({ error }); return; }
    const { config_key, config_json, description } = req.body;
    if (await repo.findByKey(config_key)) {
      res.status(409).json({ error: "Config already exists" }); return;
    }
    try {
      const row = await repo.create({ config_key, config_json, description,
        updated_by: req.header("X-User-Id")?.trim() || req.cookies?.staff_id });
      res.status(201).json(rowToApi(row));
    } catch (error) {
      const code = (error as { code?: string }).code;
      if (code !== "SQLITE_CONSTRAINT_UNIQUE" && code !== "ER_DUP_ENTRY") throw error;
      res.status(409).json({ error: "Config already exists" });
    }
  }));
  router.put("/:configKey", asyncHandler(async (req, res) => {
    const error = validateBody(req.body, false);
    if (error) { res.status(400).json({ error }); return; }
    const { config_json, enabled, description } = req.body;
    const row = await repo.update(String(req.params.configKey), { config_json, enabled, description,
      updated_by: req.header("X-User-Id")?.trim() || req.cookies?.staff_id });
    if (!row) { res.status(404).json({ error: "Config not found" }); return; }
    res.json(rowToApi(row));
  }));
  router.delete("/:configKey", asyncHandler(async (req, res) => {
    if (!await repo.delete(String(req.params.configKey))) {
      res.status(404).json({ error: "Config not found" }); return;
    }
    res.json({ affected: true });
  }));
  return router;
}

function validKey(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 64
    && value === value.trim();
}

function validateBody(body: unknown, creating: boolean): string | null {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "Request body must be an object";
  const fields = body as Record<string, unknown>;
  const allowed = creating ? ["config_key", "config_json", "description"] : ["config_json", "enabled", "description"];
  if (Object.keys(fields).some(key => !allowed.includes(key))) return "Unknown configuration field";
  if (creating && !validKey(fields.config_key)) return "config_key must contain 1–64 non-padding characters";
  if (creating || fields.config_json !== undefined) {
    if (typeof fields.config_json !== "string") return "config_json must be a JSON string";
    try { JSON.parse(fields.config_json); } catch { return "config_json must contain valid JSON"; }
  }
  if (fields.description !== undefined && typeof fields.description !== "string") return "description must be a string";
  if (fields.enabled !== undefined && fields.enabled !== 0 && fields.enabled !== 1) return "enabled must be 0 or 1";
  if (!creating && Object.keys(fields).length === 0) return "Provide config_json, enabled or description";
  return null;
}

function rowToApi(row: AppConfigRow) {
  return {
    id: row.id, configKey: row.config_key, configJson: row.config_json,
    version: row.version, enabled: row.enabled === 1, description: row.description,
    updatedBy: row.updated_by, gmtCreate: row.gmt_create, gmtModified: row.gmt_modified,
  };
}
