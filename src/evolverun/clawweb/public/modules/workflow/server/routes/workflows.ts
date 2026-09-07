/**
 * Workflows API routes — database-persisted workflow specs and YAML export.
 * ClawWeb's own endpoints (not from ClawFlow).
 */
import { Router, type Request, type Response } from "express";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import { normalizeWorkflowSpec } from "../workflow.js";
import type { WorkflowSpec } from "../workflow.js";
import type { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";
import type { FacadeBindingRepository } from "../repositories/facade-binding-repository.js";

/** Convert gmt_modified (Date | string | number) to epoch milliseconds. Returns 0 if unparseable. */
function toEpochMs(value: Date | string | number | null | undefined): number {
  if (value == null) return 0;
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value > 1e12 ? value : value * 1000;
  const s = String(value).trim();
  if (!s) return 0;
  const ms = new Date(s.includes("T") ? s : s.replace(" ", "T") + "Z").getTime();
  return isNaN(ms) ? 0 : ms;
}

/** Convert a gmt_create/timestamp field to epoch SECONDS. Handles Date objects (mysql2 returns
 *  JS Date for TIMESTAMP columns), strings, and numbers (sqlite epoch seconds / epoch ms). */
function toEpochSec(value: Date | string | number | null | undefined): number {
  return Math.floor(toEpochMs(value) / 1000);
}

/**
 * Reserved workflow IDs are namespaced with leading+trailing double underscores
 * (e.g. `__platform__`). They are reserved for platform-level resources that
 * share the workflow-shaped tables (currently `http_callback_configs`).
 *
 * Real workflow creation/import must reject these IDs to prevent collisions
 * with platform-owned rows.
 */
const RESERVED_WORKFLOW_ID_PATTERN = /^__.+__$/;
export function isReservedWorkflowId(id: string | null | undefined): boolean {
  if (!id) return false;
  return RESERVED_WORKFLOW_ID_PATTERN.test(id);
}
const RESERVED_ID_MESSAGE =
  "workflowId 使用了系统保留前缀（以 __ 开头并以 __ 结尾），请改用普通 workflow id";

/**
 * Map a raw http_callback_configs row (snake_case, notify_on as JSON string)
 * to the camelCase DTO the frontend expects (notifyOn as string[]).
 * Defensive against missing/null/invalid JSON so a bad row never crashes the UI.
 */
function mapCallbackRow(row: HttpCallbackConfigRow) {
  let notifyOn: string[];
  try {
    const parsed = row.notify_on != null ? JSON.parse(row.notify_on) : [];
    notifyOn = Array.isArray(parsed) ? parsed : [];
  } catch {
    notifyOn = [];
  }
  return {
    id: row.id,
    configId: row.config_id,
    workflowId: row.workflow_id,
    name: row.name,
    url: row.url,
    secret: row.secret,
    enabled: row.enabled === 1,
    notifyOn,
    timeoutMs: row.timeout_ms,
    maxRetries: row.max_retries,
    retryDelayMs: row.retry_delay_ms,
    includeNodeOutput: row.include_node_output === 1,
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}

import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import type { WorkflowNotificationConfigRepository } from "../repositories/workflow-notification-config-repository.js";
import type { WorkflowDeployHistoryRepository } from "../repositories/workflow-deploy-history-repository.js";
import type { HttpCallbackConfigRepository, HttpCallbackConfigRow } from "../repositories/http-callback-config-repository.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { validateSpec, validateYaml, type ValidationResult } from "../validators/workflow-validator.js";
import { ApiCache } from "../cache.js";
import {
  hasWorkflowAccess,
  requireWorkflowAccess,
  resolveWorkflowActorId,
} from "@avernet/clawweb-shared/server/services/workflow-access";

const workflowsCache = new ApiCache<unknown[]>({
  ttlMs: 2 * 60 * 1000,
  maxSize: 100,
  keyPrefix: "workflows",
});

export function createWorkflowsRouter(
  workflowSpecRepo: WorkflowSpecRepository | null,
  facadeRepo: FacadeBindingRepository | null,
  botPermRepo: BotWorkflowPermissionRepository | null,
  notificationConfigRepo: WorkflowNotificationConfigRepository | null,
  wfdhRepo: WorkflowDeployHistoryRepository | null,
  httpCallbackConfigRepo: HttpCallbackConfigRepository | null = null,
): Router {
  const router = Router();

  /** POST /save — create or update workflow spec with authorization checks */
  router.post("/save", asyncHandler(async (req: Request, res: Response) => {
    if (!workflowSpecRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { workflowId, originalWorkflowId, packId, spec, facade, botId, botOwnerId, skipDeployHistory } = req.body as {
        workflowId: string;
        originalWorkflowId?: string;
        packId?: string;
        spec: WorkflowSpec;
        facade?: { command?: string; remark?: string };
        botId?: string;
        botOwnerId?: string;
        skipDeployHistory?: boolean;
      };

      if (!workflowId || !spec || !spec.id || !Array.isArray(spec.nodes)) {
        res.status(400).json({
          error: "Bad Request",
          message: "Invalid request: workflowId and spec (with id and nodes) are required",
        });
        return;
      }

      // Reject workflow IDs that collide with reserved platform-level slots
      // (e.g. `__platform__`). See isReservedWorkflowId for the convention.
      if (isReservedWorkflowId(workflowId) || isReservedWorkflowId(originalWorkflowId)) {
        res.status(400).json({
          error: "Bad Request",
          message: RESERVED_ID_MESSAGE,
        });
        return;
      }

      // botOwnerId is required — fallback to cookie, but must not be empty
      const resolvedBotOwnerId = resolveWorkflowActorId(req) || botOwnerId?.trim() || "";
      const resolvedBotId = botId?.trim() || undefined;
      if (!resolvedBotOwnerId) {
        res.status(400).json({ error: "Bad Request", message: "botOwnerId is required" });
        return;
      }

      // Determine operation type
      const idChanged = !!originalWorkflowId && originalWorkflowId !== workflowId;
      const isUpdate = !!originalWorkflowId || (!originalWorkflowId && await workflowSpecRepo.existsByWorkflowId(workflowId));
      const permCheckId = originalWorkflowId || workflowId; // Check permission on the original workflow

      // Uniqueness check: when creating or changing ID, target workflowId must not exist
      if (idChanged || !isUpdate) {
        const exists = await workflowSpecRepo.existsByWorkflowId(workflowId);
        if (exists) {
          res.status(409).json({ error: "Conflict", message: `存在重复的 workflow id: "${workflowId}"` });
          return;
        }
      }

      // Edit permission check: on update, verify the user has edit access
      if (isUpdate && botPermRepo && !req.isAdmin) {
        const hasPermission = await botPermRepo.hasEditPermission(permCheckId, resolvedBotOwnerId);
        if (!hasPermission) {
          // When no originalWorkflowId is provided and the target id already exists,
          // the user is colliding with an existing workflow they don't own — report it
          // as a duplicate id rather than a permission error.
          if (!originalWorkflowId) {
            res.status(409).json({ error: "Conflict", message: `存在重复的 workflow id: "${workflowId}"` });
            return;
          }
          res.status(403).json({ error: "Forbidden", message: "No edit permission for this workflow" });
          return;
        }
      }

      // Fetch existing row for pack_id preservation (when request omit packId,
      // keep the DB value instead of overwriting with NULL)
      const existingRow = isUpdate ? await workflowSpecRepo.findByWorkflowId(permCheckId) : null;

      const validationResult = validateSpec(spec);
      if (!validationResult.valid) {
        res.status(400).json({
          error: "Validation Failed",
          message: `Workflow spec has ${validationResult.issues.length} validation issue${validationResult.issues.length > 1 ? "s" : ""}`,
          issues: validationResult.issues,
        });
        return;
      }
      const specJson = JSON.stringify(validationResult.normalizedSpec);

      // Execute save: handle ID change vs normal upsert
      let row;
      if (idChanged) {
        // ID changed: update by original ID + cascade related tables
        const updated = await workflowSpecRepo.updateByOriginalId(originalWorkflowId!, workflowId, packId ?? existingRow?.pack_id ?? null, specJson);
        if (!updated) {
          res.status(404).json({ error: "Not Found", message: `Workflow "${originalWorkflowId}" not found` });
          return;
        }
        row = updated;
        // Cascade: update workflow_id in related tables
        if (botPermRepo) {
          await botPermRepo.updateWorkflowId(originalWorkflowId!, workflowId);
        }
        if (facadeRepo) {
          await facadeRepo.updateWorkflowId(originalWorkflowId!, workflowId);
        }
        if (httpCallbackConfigRepo) {
          await httpCallbackConfigRepo.updateWorkflowId(originalWorkflowId!, workflowId);
        }
      } else {
        row = await workflowSpecRepo.upsert(workflowId, packId ?? existingRow?.pack_id ?? null, specJson);
      }

      // Persist facade binding (slash command) if provided and repo available
      if (facadeRepo && facade?.command) {
        await facadeRepo.upsert({
          command: facade.command,
          workflowId,
          packId: packId ?? null,
          remark: facade.remark ?? null,
        });
      } else if (facadeRepo && facade && !facade.command) {
        // Facade provided but no command — remove any existing binding for this workflow
        await facadeRepo.deleteByWorkflowId(workflowId);
      }

      // Persist bot permission if botOwnerId is available
      if (botPermRepo && resolvedBotOwnerId) {
        try {
          await botPermRepo.upsert({
            bot_id: resolvedBotId || null,
            bot_owner_id: resolvedBotOwnerId,
            workflow_id: workflowId,
            can_view: 1,
            can_execute: 1,
            can_edit: 1,
          });
        } catch (permErr) {
          // Non-fatal: permission write failure should not block workflow save
          console.error(`[clawweb] Failed to write bot permission for workflow ${workflowId}:`, permErr instanceof Error ? permErr.message : String(permErr));
        }
      }

      // Insert deploy_history record for web edits (action=edit)
      // This ensures web edits have version history for rollback support.
      // tagName="" (no git tag — web edits have no git operation).
      // deploy_number is always incrementing (MAX+1), same as deploy/rollback,
      // to avoid UK collision on (pack_id, deploy_number, workflow_id).
      // from_deploy_number = latest deploy/rollback record's deploy_number,
      // so rollback can find the git tag for script/asset recovery.
      // Skip when called from deploy command (which writes its own deploy_history record).
      if (wfdhRepo && !skipDeployHistory) {
        try {
          const currentMaxVersion = await wfdhRepo.getLatestVersion(workflowId);
          const newVersion = currentMaxVersion + 1;
          // Find the latest deploy/rollback record to get the base deploy_number
          const latestDeploy = await wfdhRepo.getLatestDeploy(
            packId ?? existingRow?.pack_id ?? workflowId, workflowId
          );
          const fromDeployNumber = latestDeploy?.deploy_number ?? null;
          // deploy_number always increments — never use 0 (causes UK collision on repeated saves)
          const dbMaxDeployNumber = await wfdhRepo.getMaxDeployNumber(
            packId ?? existingRow?.pack_id ?? workflowId, workflowId
          );
          const nextDeployNumber = dbMaxDeployNumber + 1;
          await wfdhRepo.insert({
            packId: packId ?? existingRow?.pack_id ?? workflowId,
            workflowId,
            deployNumber: nextDeployNumber,
            version: newVersion,
            tagName: "",
            action: "edit",
            fromDeployNumber: fromDeployNumber ?? undefined,
            specJson,
            botId: resolvedBotId,
            ownerId: resolvedBotOwnerId,
          });
          // Sync version back to workflow_specs
          await workflowSpecRepo.updateVersion(workflowId, newVersion);
        } catch (dhErr) {
          // Non-fatal: deploy history write failure should not block workflow save
          console.error(`[clawweb] Failed to write deploy_history for workflow ${workflowId}:`, dhErr instanceof Error ? dhErr.message : String(dhErr));
        }
      }

      workflowsCache.invalidate();
      res.json(JSON.parse(row.spec_json));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (msg.includes("Validation") || msg.includes("validation")) {
        res.status(400).json({ error: "Bad Request", message: msg });
        return;
      }
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST /validate — validate a workflow spec without saving (no auth required) */
  router.post("/validate", asyncHandler(async (req: Request, res: Response) => {
    const { spec, yaml } = req.body as { spec?: unknown; yaml?: string };

    let result: ValidationResult;

    if (spec != null) {
      if (typeof spec !== "object" || Array.isArray(spec)) {
        res.status(400).json({
          error: "Bad Request",
          message: "Request body must include either 'spec' (object) or 'yaml' (string)",
        });
        return;
      }
      result = validateSpec(spec);
    } else if (typeof yaml === "string") {
      result = validateYaml(yaml);
    } else {
      res.status(400).json({
        error: "Bad Request",
        message: "Request body must include either 'spec' (object) or 'yaml' (string)",
      });
      return;
    }

    // Validation result always returns 200 — validation failure is not an HTTP error
    res.json(result);
  }));

  /** GET /list — paginated list of workflow specs, filtered by view permission */
  router.get("/list", asyncHandler(async (req: Request, res: Response) => {
    try {
      if (!workflowSpecRepo) {
        res.json({ data: [], pagination: { page: 1, pageSize: 10, total: 0, totalPages: 0 } });
        return;
      }

      const page = Math.max(1, Number(req.query.page) || 1);
      const pageSize = Math.min(100, Math.max(10, Number(req.query.pageSize) || 10));
      const search = typeof req.query.search === "string" ? req.query.search : undefined;

      const { rows, total } = await workflowSpecRepo.findPage({ page, pageSize, search });

      // Apply permission filtering
      const queryBotOwnerId = req.query.botOwnerId as string | undefined;
      const headerUserId = req.headers["x-user-id"] as string | undefined;
      const queryBotId = req.query.botId as string | undefined;
      const botOwnerId = queryBotOwnerId?.trim() || headerUserId?.trim() || req.cookies?.staff_id?.trim() || "";
      const botId = queryBotId?.trim() || undefined;

      type ViewPerm = { restrictedIds: Set<string>; viewableIds: Set<string> } | null;
      let viewPerm: ViewPerm = null;
      if (!req.isAdmin && botPermRepo && botOwnerId) {
        viewPerm = await botPermRepo.getViewByIdsForOwner(botOwnerId, botId);
      }

      const filteredRows = viewPerm === null
        ? rows
        : rows.filter((r) => {
            if (!viewPerm!.restrictedIds.has(r.workflow_id)) return false;
            return viewPerm!.viewableIds.has(r.workflow_id);
          });

      const data = filteredRows.map((r) => ({
        workflowId: r.workflow_id,
        title: r.title ?? r.workflow_id,
        packId: r.pack_id,
        updatedAt: toEpochMs(r.gmt_modified),
      }));
      res.json({
        data,
        pagination: {
          page,
          pageSize,
          total,
          totalPages: Math.ceil(total / pageSize),
        },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — list all saved workflow specs from database, filtered by view permission */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    try {
      if (!workflowSpecRepo) {
        res.json([]);
        return;
      }

      res.set("Cache-Control", "private, max-age=60, must-revalidate");

      const queryBotOwnerId = req.query.botOwnerId as string | undefined;
      const headerUserId = req.headers["x-user-id"] as string | undefined;
      const queryBotId = req.query.botId as string | undefined;
      const botOwnerId = queryBotOwnerId?.trim() || headerUserId?.trim() || req.cookies?.staff_id?.trim() || "";
      const botId = queryBotId?.trim() || undefined;
      const cacheKey = `list:${botOwnerId}:${botId ?? ""}:${req.isAdmin ? "admin" : "user"}`;

      const cached = workflowsCache.get(cacheKey);
      if (cached) {
        res.json(cached);
        return;
      }

      const rows = await workflowSpecRepo.listSummaries();

      type ViewPerm = { restrictedIds: Set<string>; viewableIds: Set<string> } | null;
      let viewPerm: ViewPerm = null;
      if (req.isAdmin) {
        viewPerm = null;
      } else if (botPermRepo && botOwnerId) {
        viewPerm = await botPermRepo.getViewByIdsForOwner(botOwnerId, botId);
      }

      const result = rows
        .filter((r) => {
          if (viewPerm === null) return true;
          if (!viewPerm.restrictedIds.has(r.workflow_id)) return false;
          return viewPerm.viewableIds.has(r.workflow_id);
        })
        .map((r) => ({
          workflowId: r.workflow_id,
          title: r.title ?? r.workflow_id,
          packId: r.pack_id,
          updatedAt: toEpochMs(r.gmt_modified),
        }));
      workflowsCache.set(cacheKey, result);
      res.json(result);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:workflowId/history — deploy history list (no spec_json) */
  router.get("/:workflowId/history", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
    const limit = Math.min(100, Math.max(1, parseInt(req.query.limit as string ?? "20", 10)));
    if (isNaN(limit)) { res.status(400).json({ error: "Bad Request" }); return; }
    const rows = await wfdhRepo.listHistory(workflowId, limit);
    const history = rows.map((r) => ({
      deployNumber: r.deploy_number,
      version: r.version,
      tagName: r.tag_name,
      action: r.action,
      fromDeployNumber: r.from_deploy_number,
      note: r.note,
      botId: r.bot_id,
      ownerId: r.owner_id,
      isActive: !!(r as any).is_active,
      gmtCreate: toEpochSec(r.gmt_create),
    }));
    res.json({ workflowId, history });
  }));

  /** GET /:workflowId/history/diff — compare two deploy records (returns both spec_json; frontend renders diff).
   *  Accepts ?fromDeploy=&toDeploy= (deploy_number, precise, recommended) or ?from=&to= (version, legacy:
   *  resolves to the latest deploy/edit record with that version). */
  // Registered before "/:workflowId/history/:version" so the literal `diff` segment isn't captured as :version.
  router.get("/:workflowId/history/diff", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
    const fromDeploy = req.query.fromDeploy != null ? parseInt(String(req.query.fromDeploy), 10) : NaN;
    const toDeploy = req.query.toDeploy != null ? parseInt(String(req.query.toDeploy), 10) : NaN;
    const fromVersion = req.query.from != null ? parseInt(String(req.query.from), 10) : NaN;
    const toVersion = req.query.to != null ? parseInt(String(req.query.to), 10) : NaN;

    const fromRow = !isNaN(fromDeploy)
      ? await wfdhRepo.findByWorkflowAndDeployNumber(workflowId, fromDeploy)
      : !isNaN(fromVersion)
        ? await wfdhRepo.findByVersionDeployOrEdit(workflowId, fromVersion)
        : null;
    const toRow = !isNaN(toDeploy)
      ? await wfdhRepo.findByWorkflowAndDeployNumber(workflowId, toDeploy)
      : !isNaN(toVersion)
        ? await wfdhRepo.findByVersionDeployOrEdit(workflowId, toVersion)
        : null;

    if ((isNaN(fromDeploy) && isNaN(fromVersion)) || !fromRow) {
      res.status(404).json({ error: "Not Found", message: `from record not found (fromDeploy=${req.query.fromDeploy ?? ""} from=${req.query.from ?? ""})` });
      return;
    }
    if ((isNaN(toDeploy) && isNaN(toVersion)) || !toRow) {
      res.status(404).json({ error: "Not Found", message: `to record not found (toDeploy=${req.query.toDeploy ?? ""} to=${req.query.to ?? ""})` });
      return;
    }
    const fromGmt = toEpochSec(fromRow.gmt_create);
    const toGmt = toEpochSec(toRow.gmt_create);
    res.json({
      workflowId,
      from: { version: fromRow.version, deployNumber: fromRow.deploy_number, action: fromRow.action, specJson: fromRow.spec_json, gmtCreate: fromGmt },
      to:   { version: toRow.version,   deployNumber: toRow.deploy_number,   action: toRow.action,   specJson: toRow.spec_json,   gmtCreate: toGmt },
    });
  }));

  /** GET /:workflowId/history/by-deploy/:deployNumber — full snapshot of a specific deploy record. */
  // Registered before "/:workflowId/history/:version" so the literal `by-deploy` segment isn't captured as :version.
  router.get("/:workflowId/history/by-deploy/:deployNumber", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
    const deployNumber = parseInt(String(req.params.deployNumber), 10);
    if (isNaN(deployNumber)) { res.status(400).json({ error: "Bad Request" }); return; }
    const row = await wfdhRepo.findByWorkflowAndDeployNumber(workflowId, deployNumber);
    if (!row) { res.status(404).json({ error: "Not Found" }); return; }
    res.json({
      workflowId, version: row.version, deployNumber: row.deploy_number, tagName: row.tag_name,
      action: row.action, specJson: row.spec_json, note: row.note, fromDeployNumber: row.from_deploy_number, gmtCreate: toEpochSec(row.gmt_create),
    });
  }));

  /** GET /:workflowId/history/:version — full spec snapshot at a specific version */
  router.get("/:workflowId/history/:version", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
    const version = parseInt(String(req.params.version), 10);
    if (isNaN(version)) { res.status(400).json({ error: "Bad Request" }); return; }
    const row = await wfdhRepo.findByVersion(workflowId, version);
    if (!row) { res.status(404).json({ error: "Not Found" }); return; }
    const gmtCreate = toEpochSec(row.gmt_create);
    res.json({
      workflowId, version, deployNumber: row.deploy_number, tagName: row.tag_name,
      action: row.action, specJson: row.spec_json, note: row.note, gmtCreate,
    });
  }));

  /** GET /:workflowId/versions — list deployed versions (action='deploy' only, no spec_json).
   *  Returns version metadata with isActive flag for frontend version selector. */
  router.get("/:workflowId/versions", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    const limit = Math.min(100, Math.max(1, parseInt(req.query.limit as string ?? "50", 10)));
    if (isNaN(limit)) { res.status(400).json({ error: "Bad Request" }); return; }
    const rows = await wfdhRepo.listHistory(workflowId, limit);
    // Filter to deploy actions only, map to response shape
    const versions = rows
      .filter((r) => r.action === "deploy")
      .map((r) => ({
        version: r.version,
        deployNumber: r.deploy_number,
        tagName: r.tag_name,
        isActive: !!r.is_active,
        gmtCreate: toEpochSec(r.gmt_create),
      }));
    res.json({ workflowId, versions });
  }));

  /** POST /:workflowId/versions/:v/activate — set version v as the active (default) version. */
  router.post("/:workflowId/versions/:v/activate", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    const version = parseInt(String(req.params.v), 10);
    if (isNaN(version)) { res.status(400).json({ error: "Bad Request", message: "Invalid version" }); return; }
    const ok = await wfdhRepo.setActive(workflowId, version);
    if (!ok) {
      res.status(404).json({ error: "Not Found", message: `Version ${version} not found for workflow ${workflowId}` });
      return;
    }
    res.json({ workflowId, version, activated: true });
  }));

  /** GET /:workflowId — get saved workflow spec from DB, fallback to filesystem */
  router.get("/:workflowId", asyncHandler(async (req: Request, res: Response) => {
    try {
      const workflowId = String(req.params.workflowId);

      // Try database first
      if (workflowSpecRepo) {
        const row = await workflowSpecRepo.findByWorkflowId(workflowId);
        if (row) {
          const parsed = JSON.parse(row.spec_json) as Record<string, unknown>;
          let spec: Record<string, unknown>;
          // Handle {"content": "yaml-string"} wrapper format from older DB writes
          if (typeof parsed.content === "string" && !Array.isArray(parsed.nodes)) {
            try {
              const raw = parseYaml(parsed.content) as unknown;
              spec = normalizeWorkflowSpec(raw) as unknown as Record<string, unknown>;
            } catch {
              spec = parsed;
            }
          } else {
            spec = parsed;
          }

          // Attach facade binding from facade_bindings table
          if (facadeRepo) {
            const facadeRows = await facadeRepo.findByWorkflowId(workflowId);
            if (facadeRows.length > 0) {
              const binding = facadeRows[0];
              spec.facade = { command: binding.command, remark: binding.remark ?? undefined };
            }
          }

          // Attach DB metadata for timestamp comparison by ClawMind.
          // gmt_modified type depends on DB column type and driver:
          //   MySQL TIMESTAMP → mysql2 returns JS Date object
          //   MySQL DATETIME  → mysql2 returns string "2026-07-18 13:15:18"
          //   SQLite          → number (epoch seconds)
          const updatedAtMs = toEpochMs(row.gmt_modified);
          if (updatedAtMs > 0) {
            spec.updatedAt = updatedAtMs;
          }

          res.json(spec);
          return;
        }
      }

      res.status(404).json({ error: "Not Found", message: `Workflow "${workflowId}" not found` });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:workflowId/export — export workflow spec as downloadable YAML */
  router.get("/:workflowId/export", asyncHandler(async (req: Request, res: Response) => {
    try {
      let spec: WorkflowSpec | null = null;

      // Try database first
      if (workflowSpecRepo) {
        const row = await workflowSpecRepo.findByWorkflowId(String(req.params.workflowId));
        if (row) {
          const parsed = JSON.parse(row.spec_json) as Record<string, unknown>;
          // Handle {"content": "yaml-string"} wrapper format from older DB writes
          if (typeof parsed.content === "string" && !Array.isArray(parsed.nodes)) {
            try {
              const raw = parseYaml(parsed.content) as unknown;
              spec = normalizeWorkflowSpec(raw);
            } catch {
              spec = parsed as unknown as WorkflowSpec;
            }
          } else {
            spec = parsed as unknown as WorkflowSpec;
          }
        }
      }

      if (!spec) {
        res.status(404).json({ error: "Not Found", message: `Workflow "${req.params.workflowId}" not found` });
        return;
      }

      const yaml = stringifyYaml(spec, { lineWidth: 0 });
      res.setHeader("Content-Type", "application/x-yaml");
      res.setHeader("Content-Disposition", `attachment; filename="${req.params.workflowId}.yaml"`);
      res.send(yaml);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:workflowId — delete a workflow spec from database */
  router.delete("/:workflowId", asyncHandler(async (req: Request, res: Response) => {
    if (!workflowSpecRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const workflowId = String(req.params.workflowId);
      if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;

      const deleted = await workflowSpecRepo.delete(workflowId);
      if (!deleted) {
        res.status(404).json({ error: "Not Found", message: `Workflow "${workflowId}" not found` });
        return;
      }

      // Cascade: clean up related facade bindings, permissions, and notification configs
      if (facadeRepo) {
        await facadeRepo.deleteByWorkflowId(workflowId);
      }
      if (botPermRepo) {
        const perms = await botPermRepo.findByWorkflowId(workflowId);
        for (const perm of perms) {
          await botPermRepo.delete(perm.bot_id, perm.bot_owner_id, workflowId);
        }
      }
      if (notificationConfigRepo) {
        await notificationConfigRepo.delete(workflowId);
      }
      if (httpCallbackConfigRepo) {
        await httpCallbackConfigRepo.deleteByWorkflowId(workflowId);
      }

      workflowsCache.invalidate();
      res.json({ ok: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:workflowId/access — current user's workflow-level access. */
  router.get("/:workflowId/access", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    const canView = await hasWorkflowAccess(req, botPermRepo, workflowId, "view");
    const canEdit = await hasWorkflowAccess(req, botPermRepo, workflowId, "edit");
    res.json({ workflowId, canView, canEdit });
  }));

  /** GET /:workflowId/bot-permissions — list bot permissions for a workflow editor */
  router.get("/:workflowId/bot-permissions", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    try {
      if (!botPermRepo) {
        res.json([]);
        return;
      }
      const rows = await botPermRepo.findByWorkflowId(workflowId);
      res.json(rows.map((r) => ({
        id: r.id,
        botId: r.bot_id,
        botOwnerId: r.bot_owner_id,
        canView: r.can_view,
        canExecute: r.can_execute,
        canEdit: r.can_edit,
      })));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:workflowId/bot-permissions — upsert bot permission for a workflow editor */
  router.put("/:workflowId/bot-permissions", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    if (!botPermRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { botId, botOwnerId, canView, canExecute, canEdit } = req.body as {
        botId?: string | null;
        botOwnerId?: string;
        canView?: number;
        canExecute?: number;
        canEdit?: number;
      };

      if (!botOwnerId) {
        res.status(400).json({ error: "Bad Request", message: "Missing required field: botOwnerId" });
        return;
      }

      // botId can be null/empty (owner-level permission) or a string (bot-level permission)
      if (botId !== null && botId !== undefined && typeof botId !== "string") {
        res.status(400).json({ error: "Bad Request", message: "botId must be a string or null" });
        return;
      }
      // Normalize: empty string → null (owner-level permission)
      const normalizedBotId = (!botId || botId.trim() === "") ? null : botId;

      if (![0, 1].includes(canView ?? -1) || ![0, 1].includes(canExecute ?? -1) || ![0, 1].includes(canEdit ?? -1)) {
        res.status(400).json({ error: "Bad Request", message: "canView, canExecute, canEdit must be 0 or 1" });
        return;
      }

      await botPermRepo.upsert({
        bot_id: normalizedBotId,
        bot_owner_id: botOwnerId,
        workflow_id: workflowId,
        can_view: canView!,
        can_execute: canExecute!,
        can_edit: canEdit!,
      });

      workflowsCache.invalidate();
      res.json({ ok: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:workflowId/bot-permissions — delete bot permission for a workflow editor */
  router.delete("/:workflowId/bot-permissions", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    if (!botPermRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const queryPermissionId = req.query.permissionId as string | undefined;
      const queryBotId = req.query.botId as string | undefined;
      const queryBotOwnerId = req.query.botOwnerId as string | undefined;

      const permissionId = queryPermissionId == null ? null : Number(queryPermissionId);
      if (queryPermissionId != null && (!Number.isInteger(permissionId) || Number(permissionId) <= 0)) {
        res.status(400).json({ error: "Bad Request", message: "permissionId must be a positive integer" });
        return;
      }
      if (permissionId == null && !queryBotOwnerId) {
        res.status(400).json({ error: "Bad Request", message: "Missing required query parameter: botOwnerId" });
        return;
      }

      // Normalize: empty string → null (owner-level permission)
      const normalizedBotId = (!queryBotId || queryBotId.trim() === "") ? null : queryBotId;

      const deleted = permissionId == null
        ? await botPermRepo.delete(normalizedBotId, queryBotOwnerId!, workflowId)
        : await botPermRepo.deleteById(permissionId, workflowId);
      if (!deleted) {
        res.status(404).json({ error: "Not Found", message: "Permission record not found" });
        return;
      }

      workflowsCache.invalidate();
      res.json({ ok: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET /:workflowId/notification-config — get notification config for a workflow editor */
  router.get("/:workflowId/notification-config", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    try {
      if (!notificationConfigRepo) {
        res.json(null);
        return;
      }
      const row = await notificationConfigRepo.findByWorkflowId(workflowId);
      if (!row) {
        res.json(null);
        return;
      }
      res.json({
        workflowId: row.workflow_id,
        robotCode: row.robot_code,
        appSecret: row.app_secret,
        onFailureUsers: JSON.parse(row.on_failure_users),
        onFailureGroups: JSON.parse(row.on_failure_groups),
        onFailureMessageTitle: row.on_failure_message_title,
        onFailureMessageIncludeRunLink: row.on_failure_message_include_run_link === 1,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:workflowId/notification-config — upsert notification config for a workflow editor */
  router.put("/:workflowId/notification-config", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    if (!notificationConfigRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { robotCode, appSecret, onFailureUsers, onFailureGroups, onFailureMessageTitle, onFailureMessageIncludeRunLink } = req.body as {
        robotCode?: string;
        appSecret?: string;
        onFailureUsers?: Array<{ userId: string; name?: string }>;
        onFailureGroups?: Array<{ openConversationId: string; name?: string }>;
        onFailureMessageTitle?: string | null;
        onFailureMessageIncludeRunLink?: boolean;
      };

      if (!robotCode || !appSecret) {
        res.status(400).json({ error: "Bad Request", message: "robotCode and appSecret are required" });
        return;
      }

      if (!Array.isArray(onFailureUsers) || !Array.isArray(onFailureGroups)) {
        res.status(400).json({ error: "Bad Request", message: "onFailureUsers and onFailureGroups must be arrays" });
        return;
      }

      await notificationConfigRepo.upsert(workflowId, {
        robotCode,
        appSecret,
        onFailureUsers,
        onFailureGroups,
        onFailureMessageTitle: onFailureMessageTitle ?? null,
        onFailureMessageIncludeRunLink: onFailureMessageIncludeRunLink !== false,
      });

      workflowsCache.invalidate();
      res.json({ ok: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:workflowId/notification-config — delete notification config for a workflow editor */
  router.delete("/:workflowId/notification-config", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    if (!notificationConfigRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const deleted = await notificationConfigRepo.delete(workflowId);
      if (!deleted) {
        res.status(404).json({ error: "Not Found", message: "Notification config not found" });
        return;
      }
      workflowsCache.invalidate();
      res.json({ ok: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  // ── HTTP Callback Config endpoints ──────────────────────────

  /** GET /:workflowId/callback-configs — list all HTTP callback configs for a workflow */
  router.get("/:workflowId/callback-configs", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    if (!httpCallbackConfigRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const rows = await httpCallbackConfigRepo.findByWorkflowId(workflowId);
      // Map rows to camelCase DTOs and mask secrets — only show last 4 chars
      const masked = rows.map((row) => {
        const dto = mapCallbackRow(row);
        return { ...dto, secret: dto.secret.length > 4 ? `****${dto.secret.slice(-4)}` : "****" };
      });
      res.json(masked);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error("[workflows] GET callback-configs failed:", msg);
      res.status(500).json({ error: "Internal Server Error", message: "Internal error" });
    }
  }));

  /** POST /:workflowId/callback-configs — create a new HTTP callback config */
  router.post("/:workflowId/callback-configs", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    if (!httpCallbackConfigRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { name, url, secret, notifyOn, enabled, timeoutMs, maxRetries, retryDelayMs, includeNodeOutput } = req.body as {
        name: string;
        url: string;
        secret?: string;
        notifyOn: string[];
        enabled?: boolean;
        timeoutMs?: number;
        maxRetries?: number;
        retryDelayMs?: number;
        includeNodeOutput?: boolean;
      };

      if (!name || !url || !Array.isArray(notifyOn) || notifyOn.length === 0) {
        res.status(400).json({ error: "Bad Request", message: "name, url, and notifyOn (non-empty array) are required" });
        return;
      }
      // Validate URL format
      try { new URL(url); } catch { res.status(400).json({ error: "Bad Request", message: "url must be a valid URL" }); return; }
      // Validate notifyOn event values
      const VALID_NOTIFY_EVENTS = new Set(["workflow_started", "node_started", "node_succeeded", "node_failed", "node_skipped", "workflow_succeeded", "workflow_failed"]);
      const invalidEvents = notifyOn.filter((e: string) => !VALID_NOTIFY_EVENTS.has(e));
      if (invalidEvents.length > 0) {
        res.status(400).json({ error: "Bad Request", message: `Invalid notifyOn events: ${invalidEvents.join(", ")}` });
        return;
      }

      const configId = `cfg:${workflowId}:${Date.now().toString(36)}`;
      const row = await httpCallbackConfigRepo.insert({
        configId,
        workflowId,
        name,
        url,
        secret: secret ?? "",
        enabled,
        notifyOn,
        timeoutMs,
        maxRetries,
        retryDelayMs,
        includeNodeOutput,
      });
      workflowsCache.invalidate();
      const dto = mapCallbackRow(row);
      res.status(201).json({ ...dto, secret: dto.secret.length > 4 ? `****${dto.secret.slice(-4)}` : "****" });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error("[workflows] POST callback-configs failed:", msg);
      res.status(500).json({ error: "Internal Server Error", message: "Internal error" });
    }
  }));

  /** PUT /:workflowId/callback-configs/:configId — update an HTTP callback config */
  router.put("/:workflowId/callback-configs/:configId", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    if (!httpCallbackConfigRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const configId = String(req.params.configId);
      const existing = await httpCallbackConfigRepo.findByConfigId(configId);
      if (!existing) {
        res.status(404).json({ error: "Not Found", message: "Callback config not found" });
        return;
      }

      const { name, url, secret, notifyOn, enabled, timeoutMs, maxRetries, retryDelayMs, includeNodeOutput } = req.body as Record<string, unknown>;
      const updated = await httpCallbackConfigRepo.update(configId, {
        ...(typeof name === "string" ? { name } : {}),
        ...(typeof url === "string" ? { url } : {}),
        ...(typeof secret === "string" ? { secret } : {}),
        ...(Array.isArray(notifyOn) ? { notifyOn: notifyOn as string[] } : {}),
        ...(typeof enabled === "boolean" ? { enabled } : {}),
        ...(typeof timeoutMs === "number" ? { timeoutMs } : {}),
        ...(typeof maxRetries === "number" ? { maxRetries } : {}),
        ...(typeof retryDelayMs === "number" ? { retryDelayMs } : {}),
        ...(typeof includeNodeOutput === "boolean" ? { includeNodeOutput } : {}),
      });
      workflowsCache.invalidate();
      const dto = updated ? mapCallbackRow(updated) : null;
      res.json(dto ? { ...dto, secret: dto.secret.length > 4 ? `****${dto.secret.slice(-4)}` : "****" } : { ok: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error("[workflows] PUT callback-configs failed:", msg);
      res.status(500).json({ error: "Internal Server Error", message: "Internal error" });
    }
  }));

  /** DELETE /:workflowId/callback-configs/:configId — delete an HTTP callback config */
  router.delete("/:workflowId/callback-configs/:configId", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
    if (!httpCallbackConfigRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const configId = String(req.params.configId);
      const deleted = await httpCallbackConfigRepo.deleteByConfigId(configId);
      if (!deleted) {
        res.status(404).json({ error: "Not Found", message: "Callback config not found" });
        return;
      }
      workflowsCache.invalidate();
      res.json({ ok: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.error("[workflows] DELETE callback-configs failed:", msg);
      res.status(500).json({ error: "Internal Server Error", message: "Internal error" });
    }
  }));

  return router;
}
