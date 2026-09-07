/**
 * Bench API routes — domain-aware template management, upload, run tracking.
 */
import { Router, type Request, type Response } from "express";
import { createHash } from "node:crypto";
import multer from "multer";
import JSZip from "jszip";
import type { BenchDomainRepository } from "../repositories/bench-domain-repository.js";
import type { BenchTemplateRepository } from "../repositories/bench-template-repository.js";
import type { BenchTemplateVersionRepository } from "../repositories/bench-template-version-repository.js";
import type { BenchRunRepository } from "../repositories/bench-run-repository.js";
import type { BenchTaskResultRepository } from "../repositories/bench-task-result-repository.js";
import type { BenchArtifactRepository, BenchArtifactRow } from "../repositories/bench-artifact-repository.js";
import type { BenchTagRepository, BenchTagRow, BenchDomainTagRow, DomainKey } from "../repositories/bench-tag-repository.js";
import { parseBenchMarkdown } from "../utils/bench-markdown-parser.js";
import { validateClawBenchRuntimeTemplate } from "../utils/clawbench-template-validator.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

const upload = multer({ storage: multer.memoryStorage() });
const MAX_ARTIFACT_BYTES = 10 * 1024 * 1024;

export function createBenchRouter(
  domainRepo: BenchDomainRepository | null,
  templateRepo: BenchTemplateRepository | null,
  versionRepo: BenchTemplateVersionRepository | null,
  runRepo: BenchRunRepository | null,
  resultRepo: BenchTaskResultRepository | null,
  db: import("@avernet/clawweb-shared/server/db").IDatabase | null,
  artifactRepo: BenchArtifactRepository | null = null,
  tagRepo: BenchTagRepository | null = null,
): Router {
  const router = Router();

  // ── Helpers ──

  function getCurrentUserId(req: Request): string | null {
    const raw = req.headers["x-user-id"];
    if (typeof raw === "string" && raw) return raw;
    return null;
  }

  function resolveDomainRef(domainIdRef: string, currentUserId: string | null): { ownerUserId: string; domainId: string } {
    const match = domainIdRef.match(/^(\d+)_(.+)$/);
    if (match) {
      return { ownerUserId: match[1], domainId: match[2] };
    }
    if (!currentUserId) {
      throw new Error("Unauthorized: missing user identity");
    }
    return { ownerUserId: currentUserId, domainId: domainIdRef };
  }

  function resolveDomainParams(req: Request): { ownerUserId: string; domainId: string } {
    if (req.params.ownerUserId) {
      return {
        ownerUserId: String(req.params.ownerUserId),
        domainId: String(req.params.domainId),
      };
    }
    return resolveDomainRef(String(req.params.domainId), getCurrentUserId(req));
  }

  function requireAuth(res: Response, userId: string | null): userId is string {
    if (!userId) {
      res.status(401).json({ error: "Unauthorized", message: "Missing user identity" });
      return false;
    }
    return true;
  }

  function requireOwner(res: Response, currentUserId: string, ownerUserId: string): boolean {
    if (currentUserId !== ownerUserId) {
      res.status(403).json({ error: "Forbidden", message: "You can only modify your own domains" });
      return false;
    }
    return true;
  }

  function requireBenchAdmin(req: Request, res: Response): boolean {
    if (!req.isAdmin && !req.isBenchAdmin) {
      res.status(403).json({ error: "Forbidden", message: "Bench admin permission required" });
      return false;
    }
    return true;
  }

  // ── Admin Overview ──

  router.get("/admin/summary", asyncHandler(async (req: Request, res: Response) => {
    if (!db) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    const { where, values, join } = buildAdminRunFilter(req.query);
    const rows = await db.query<{
      total_run_count: number;
      succeeded_count: number | null;
      failed_count: number | null;
      running_count: number | null;
      avg_pass_rate: number | null;
      avg_score: number | null;
    }>(
      `SELECT COUNT(*) AS total_run_count,
              SUM(CASE WHEN r.status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
              SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
              SUM(CASE WHEN r.status IN ('pending', 'running') THEN 1 ELSE 0 END) AS running_count,
              AVG(r.pass_rate) AS avg_pass_rate,
              AVG(r.score) AS avg_score
       FROM cm_bench_runs r ${join} ${where}`,
      values,
    );
    const templateFilter = buildAdminTemplateFilter(req.query, { includeStatus: false });
    const templateRows = await db.query<{
      owner_count: number;
      domain_count: number;
      template_count: number;
    }>(
      `SELECT COUNT(DISTINCT t.owner_user_id) AS owner_count,
              COUNT(DISTINCT t.domain_id) AS domain_count,
              COUNT(*) AS template_count
       FROM cm_bench_templates t ${templateFilter.join} ${templateFilter.where}`,
      templateFilter.values,
    );
    const row = rows[0];
    const templateRow = templateRows[0];
    res.json({
      totalRunCount: Number(row?.total_run_count ?? 0),
      succeededCount: Number(row?.succeeded_count ?? 0),
      failedCount: Number(row?.failed_count ?? 0),
      runningCount: Number(row?.running_count ?? 0),
      avgPassRate: row?.avg_pass_rate == null ? null : Number(row.avg_pass_rate),
      avgScore: row?.avg_score == null ? null : Number(row.avg_score),
      ownerCount: Number(templateRow?.owner_count ?? 0),
      domainCount: Number(templateRow?.domain_count ?? 0),
      templateCount: Number(templateRow?.template_count ?? 0),
    });
  }));

  router.get("/admin/daily", asyncHandler(async (req: Request, res: Response) => {
    if (!db) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    const nowSec = Math.floor(Date.now() / 1000);
    const from = req.query.from ? Number(req.query.from) : nowSec - 14 * 24 * 3600;
    const to = req.query.to ? Number(req.query.to) : nowSec;
    const query = { ...req.query, startedFrom: String(from), startedTo: String(to) };
    const { where, values, join } = buildAdminRunFilter(query);
    const dayExpr = db.dbType === "mysql" || db.dbType === "zdas"
      ? "DATE_FORMAT(FROM_UNIXTIME(r.started_at), '%Y-%m-%d')"
      : "date(r.started_at, 'unixepoch')";
    const rows = await db.query<{
      day: string;
      run_count: number;
      succeeded_count: number | null;
      failed_count: number | null;
      running_count: number | null;
      avg_pass_rate: number | null;
    }>(
      `SELECT ${dayExpr} AS day,
              COUNT(*) AS run_count,
              SUM(CASE WHEN r.status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded_count,
              SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
              SUM(CASE WHEN r.status IN ('pending', 'running') THEN 1 ELSE 0 END) AS running_count,
              AVG(r.pass_rate) AS avg_pass_rate
       FROM cm_bench_runs r ${join} ${where}${where ? " AND" : " WHERE"} r.started_at IS NOT NULL
       GROUP BY ${dayExpr}
       ORDER BY day ASC`,
      values,
    );
    res.json({
      from,
      to,
      days: rows.map((row) => ({
        date: row.day,
        runCount: Number(row.run_count ?? 0),
        succeededCount: Number(row.succeeded_count ?? 0),
        failedCount: Number(row.failed_count ?? 0),
        runningCount: Number(row.running_count ?? 0),
        avgPassRate: row.avg_pass_rate == null ? null : Number(row.avg_pass_rate),
      })),
    });
  }));

  router.get("/admin/samples", asyncHandler(async (req: Request, res: Response) => {
    if (!db || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    const limit = Math.max(1, Math.min(Number(req.query.limit ?? 50) || 50, 200));
    const offset = Math.max(0, Number(req.query.offset ?? 0) || 0);
    const templateFilter = buildAdminTemplateFilter(req.query, { includeStatus: false });
    const runJoin = buildAdminRunJoinFilter(req.query);
    const rows = await db.query<{
      owner_user_id: string;
      domain_id: string;
      template_name: string;
      target_type: string | null;
      run_count: number;
      latest_run_at: number | null;
      avg_pass_rate: number | null;
      failed_run_count: number | null;
    }>(
      `SELECT t.owner_user_id, t.domain_id, t.template_name,
              t.target_type AS target_type,
              COUNT(r.bench_run_id) AS run_count,
              MAX(COALESCE(r.started_at, r.gmt_create)) AS latest_run_at,
              AVG(r.pass_rate) AS avg_pass_rate,
              SUM(CASE WHEN r.status = 'failed' THEN 1 ELSE 0 END) AS failed_run_count
       FROM cm_bench_templates t ${templateFilter.join}
       LEFT JOIN cm_bench_runs r ON ${runJoin.on}
       ${templateFilter.where}
       GROUP BY t.owner_user_id, t.domain_id, t.template_name, t.target_type
       ORDER BY COALESCE(latest_run_at, t.gmt_modified, t.gmt_create) DESC
       LIMIT ${limit} OFFSET ${offset}`,
      [...templateFilter.joinValues, ...runJoin.values, ...templateFilter.whereValues],
    );
    const countRows = await db.query<{ cnt: number }>(
      `SELECT COUNT(*) AS cnt
       FROM cm_bench_templates t ${templateFilter.join} ${templateFilter.where}`,
      templateFilter.values,
    );
    const keys = rows.map((row) => ({
      ownerUserId: dbText(row.owner_user_id),
      domainId: dbText(row.domain_id),
    }));
    const tagsByDomain = await safeListDomainTags(tagRepo, keys);
    const samples = await Promise.all(rows.map(async (row) => {
      const ownerUserId = dbText(row.owner_user_id);
      const domainId = dbText(row.domain_id);
      const templateName = dbText(row.template_name);
      const latestRows = await runRepo.listAll({ ownerUserId, domainId, templateName, limit: 1 });
      const latestRun = latestRows[0] ? runRowToApi(latestRows[0]) : null;
      return {
        ownerUserId,
        domainId,
        templateName,
        targetType: dbNullableText(row.target_type),
        tags: tagsByDomain.get(domainKey(ownerUserId, domainId)) ?? [],
        runCount: Number(row.run_count ?? 0),
        latestRunAt: row.latest_run_at == null ? null : Number(row.latest_run_at),
        latestStatus: latestRun?.status ?? null,
        latestPassRate: latestRun?.passRate ?? null,
        avgPassRate: row.avg_pass_rate == null ? null : Number(row.avg_pass_rate),
        failedRunCount: Number(row.failed_run_count ?? 0),
      };
    }));
    res.json({ samples, total: Number(countRows[0]?.cnt ?? 0), limit, offset });
  }));

  router.get("/admin/domains", asyncHandler(async (req: Request, res: Response) => {
    if (!domainRepo || !templateRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!req.isClawEvolveAdmin && !requireBenchAdmin(req, res)) return;
    const ownerUserId = getQueryText(req.query.ownerUserId);
    const domainText = getQueryText(req.query.domainId);
    const tagId = getQueryText(req.query.tagId);
    const rows = await domainRepo.listAll(ownerUserId, { status: "active" });
    const filteredRows = domainText
      ? rows.filter((row) => dbText(row.domain_id).includes(domainText))
      : rows;
    const keys = filteredRows.map((row) => ({
      ownerUserId: dbText(row.owner_user_id),
      domainId: dbText(row.domain_id),
    }));
    const tagsByDomain = await safeListDomainTags(tagRepo, keys);
    const domains = await Promise.all(filteredRows.map(async (row) => {
      const rowOwnerUserId = dbText(row.owner_user_id);
      const rowDomainId = dbText(row.domain_id);
      const tags = tagsByDomain.get(domainKey(rowOwnerUserId, rowDomainId)) ?? [];
      if (tagId && !tags.some((tag) => tag.tagId === tagId)) return null;
      const templateCount = await templateRepo.countByOwnerAndDomain(rowOwnerUserId, rowDomainId);
      return {
        ownerUserId: rowOwnerUserId,
        domainId: rowDomainId,
        name: dbText(row.name),
        status: dbText(row.status),
        templateCount,
        tags,
      };
    }));
    const visibleDomains = domains.filter((domain): domain is NonNullable<typeof domain> => !!domain);
    visibleDomains.sort((a, b) =>
      compareAdminText(firstTagName(a.tags), firstTagName(b.tags))
      || compareAdminText(a.domainId, b.domainId)
      || compareAdminText(a.ownerUserId, b.ownerUserId),
    );
    res.json({ domains: visibleDomains, total: visibleDomains.length });
  }));

  router.get("/admin/runs", asyncHandler(async (req: Request, res: Response) => {
    if (!runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const currentUserId = getCurrentUserId(req);
    if (!requireAuth(res, currentUserId)) return;
    if (!req.isClawEvolveAdmin) {
      res.status(403).json({ error: "Forbidden", message: "ClawEvolve admin permission required" });
      return;
    }
    const filters = {
      ownerUserId: req.query.ownerUserId ? String(req.query.ownerUserId) : undefined,
      domainId: req.query.domainId ? String(req.query.domainId) : undefined,
      templateName: req.query.templateName ? String(req.query.templateName) : undefined,
      status: req.query.status ? String(req.query.status) : undefined,
      model: req.query.model ? String(req.query.model) : undefined,
      suite: req.query.suite ? String(req.query.suite) : undefined,
      scene: req.query.scene ? String(req.query.scene) : undefined,
      startedFrom: req.query.startedFrom ? Number(req.query.startedFrom) : undefined,
      startedTo: req.query.startedTo ? Number(req.query.startedTo) : undefined,
      limit: req.query.limit ? Math.min(Number(req.query.limit), 200) : 50,
      offset: req.query.offset ? Number(req.query.offset) : 0,
    };
    const [rows, total] = await Promise.all([
      runRepo.listAll(filters),
      runRepo.count(filters),
    ]);
    res.json({
      runs: rows.map(runRowToApi),
      total,
      limit: filters.limit,
      offset: filters.offset,
    });
  }));

  router.get("/admin/tags", asyncHandler(async (req: Request, res: Response) => {
    if (!tagRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    const includeArchived = String(req.query.includeArchived ?? "").toLowerCase() === "true";
    const tags = await safeListTags(tagRepo, includeArchived);
    res.json(tags.map(tagRowToApi));
  }));

  router.post("/admin/tags", asyncHandler(async (req: Request, res: Response) => {
    if (!tagRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    const currentUserId = getCurrentUserId(req);
    const { tagId, name, description } = req.body as Record<string, unknown>;
    if (!tagId || !name) { res.status(400).json({ error: "Missing tagId or name" }); return; }
    const row = await tagRepo.create({
      tagId: normalizeTagId(String(tagId)),
      name: String(name),
      description: description == null ? null : String(description),
      createdBy: currentUserId,
    });
    res.status(201).json(tagRowToApi(row));
  }));

  router.put("/admin/tags/:tagId", asyncHandler(async (req: Request, res: Response) => {
    if (!tagRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    const row = await tagRepo.update(String(req.params.tagId), req.body as {
      name?: string;
      description?: string | null;
      status?: string;
    });
    if (!row) { res.status(404).json({ error: "Tag not found" }); return; }
    res.json(tagRowToApi(row));
  }));

  router.post("/admin/domains/tags/batch", asyncHandler(async (req: Request, res: Response) => {
    if (!tagRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    const currentUserId = getCurrentUserId(req);
    const { domains, tagIds } = req.body as { domains?: DomainKey[]; tagIds?: string[] };
    const normalizedDomains = normalizeDomainKeys(domains);
    const normalizedTagIds = normalizeTagIds(tagIds);
    if (normalizedDomains.length === 0 || normalizedTagIds.length === 0) {
      res.status(400).json({ error: "Missing domains or tagIds" }); return;
    }
    try {
      const affected = await tagRepo.addDomainTags({ domains: normalizedDomains, tagIds: normalizedTagIds, taggedBy: currentUserId });
      res.json({ affected });
    } catch (error) {
      if (isMissingBenchTagTable(error)) {
        res.status(503).json({ error: "Bench domain tag table missing", table: "cm_bench_domain_tags" });
        return;
      }
      throw error;
    }
  }));

  router.delete("/admin/domains/tags/batch", asyncHandler(async (req: Request, res: Response) => {
    if (!tagRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    const { domains, tagIds } = req.body as { domains?: DomainKey[]; tagIds?: string[] };
    const normalizedDomains = normalizeDomainKeys(domains);
    const normalizedTagIds = normalizeTagIds(tagIds);
    if (normalizedDomains.length === 0 || normalizedTagIds.length === 0) {
      res.status(400).json({ error: "Missing domains or tagIds" }); return;
    }
    try {
      const affected = await tagRepo.removeDomainTags({ domains: normalizedDomains, tagIds: normalizedTagIds });
      res.json({ affected });
    } catch (error) {
      if (isMissingBenchTagTable(error)) {
        res.status(503).json({ error: "Bench domain tag table missing", table: "cm_bench_domain_tags" });
        return;
      }
      throw error;
    }
  }));

  router.get("/admin/templates/export", asyncHandler(async (req: Request, res: Response) => {
    if (!db) { res.status(503).json({ error: "Service Unavailable" }); return; }
    if (!requireBenchAdmin(req, res)) return;
    if (!getQueryText(req.query.ownerUserId) || !getQueryText(req.query.domainId)) {
      res.status(400).json({ error: "Missing ownerUserId or domainId", message: "Template export must be scoped to one domain." });
      return;
    }
    const versionMode = parseVersionMode(req.query.versionMode);
    const limit = Math.max(1, Math.min(Number(req.query.limit ?? 500) || 500, versionMode === "all_versions" ? 2000 : 500));
    const { where, values, join } = buildAdminTemplateFilter(req.query, { includeStatus: true });
    const versionClause = versionMode === "published"
      ? "v.version = t.published_version"
      : versionMode === "latest"
        ? "v.version = t.latest_version"
        : "1 = 1";
    const rows = await db.query<{
      owner_user_id: string;
      domain_id: string;
      template_name: string;
      version: number;
      status: string;
      content_md: string;
    }>(
      `SELECT t.owner_user_id, t.domain_id, t.template_name, v.version, v.status, v.content_md
       FROM cm_bench_templates t
       JOIN cm_bench_template_versions v
         ON v.owner_user_id = t.owner_user_id AND v.domain_id = t.domain_id AND v.template_name = t.template_name
       ${join}
       ${where}${where ? " AND" : " WHERE"} ${versionClause}
       ORDER BY t.owner_user_id, t.domain_id, t.template_name, v.version DESC
       LIMIT ${limit + 1}`,
      values,
    );
    if (rows.length > limit) {
      res.status(400).json({ error: "Too many templates", message: `Export limit is ${limit}. Narrow filters and retry.` });
      return;
    }
    const keys = rows.map((row) => ({ ownerUserId: dbText(row.owner_user_id), domainId: dbText(row.domain_id) }));
    const tagsByDomain = await safeListDomainTags(tagRepo, keys);
    const zip = new JSZip();
    const manifest = {
      exportedAt: Date.now(),
      versionMode,
      templates: rows.map((row) => {
        const ownerUserId = dbText(row.owner_user_id);
        const domainId = dbText(row.domain_id);
        const templateName = dbText(row.template_name);
        const filename = versionMode === "all_versions"
          ? `${safePathSegment(templateName)}.v${row.version}.md`
          : `${safePathSegment(templateName)}.md`;
        const sourcePath = `${safePathSegment(ownerUserId)}/${safePathSegment(domainId)}/${filename}`;
        zip.file(sourcePath, dbText(row.content_md));
        return {
          ownerUserId,
          domainId,
          templateName,
          version: row.version,
          status: dbText(row.status),
          sourcePath,
          tags: tagsByDomain.get(domainKey(ownerUserId, domainId)) ?? [],
        };
      }),
    };
    zip.file("manifest.json", JSON.stringify(manifest, null, 2));
    const content = await zip.generateAsync({ type: "nodebuffer" });
    const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    res.setHeader("Content-Type", "application/zip");
    res.setHeader("Content-Disposition", `attachment; filename="clawbench-templates-export-${date}.zip"`);
    res.send(content);
  }));

  // ── Domains ──

  router.get("/domains", asyncHandler(async (req: Request, res: Response) => {
    if (!domainRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const includeArchived = String(req.query.includeArchived ?? "").toLowerCase() === "true";
      const status = req.query.status ? String(req.query.status) : undefined;
      const rows = await domainRepo.listAll(currentUserId, { status, includeArchived });
      const withCounts = await Promise.all(
        rows.map(async (d) => {
          const ownerUserId = dbText(d.owner_user_id);
          const domainId = dbText(d.domain_id);
          const count = templateRepo ? await templateRepo.countByOwnerAndDomain(ownerUserId, domainId) : 0;
          return { ...domainRowToApi(d), templateCount: count };
        }),
      );
      res.json(withCounts);
    } catch (err) { handleError(res, err); }
  }));

  router.post("/domains", asyncHandler(async (req: Request, res: Response) => {
    if (!domainRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const { domainId, name, description } = req.body;
      if (!domainId || !name) { res.status(400).json({ error: "Missing domainId or name" }); return; }

      const normalizedDomainId = String(domainId).trim();
      if (!normalizedDomainId) { res.status(400).json({ error: "Missing domainId or name" }); return; }

      const existing = await domainRepo.findByOwnerAndDomainId(currentUserId, normalizedDomainId);
      if (existing) { res.status(409).json({ error: "Domain already exists" }); return; }

      const row = await domainRepo.create({ domainId: normalizedDomainId, name: String(name), description, ownerUserId: currentUserId });
      res.status(201).json(domainRowToApi(row));
    } catch (err) { handleError(res, err); }
  }));

  router.get(["/domains/:ownerUserId/:domainId", "/domains/:domainId"], asyncHandler(async (req: Request, res: Response) => {
    if (!domainRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const resolved = resolveDomainParams(req);
      const row = await domainRepo.findByOwnerAndDomainId(resolved.ownerUserId, resolved.domainId);
      if (!row) { res.status(404).json({ error: "Domain not found" }); return; }
      res.json(domainRowToApi(row));
    } catch (err) { handleError(res, err); }
  }));

  router.put(["/domains/:ownerUserId/:domainId", "/domains/:domainId"], asyncHandler(async (req: Request, res: Response) => {
    if (!domainRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const resolved = resolveDomainParams(req);
      if (!requireOwner(res, currentUserId, resolved.ownerUserId)) return;

      const { name, description } = req.body;
      const row = await domainRepo.update(resolved.ownerUserId, resolved.domainId, { name, description });
      if (!row) { res.status(404).json({ error: "Domain not found" }); return; }
      res.json(domainRowToApi(row));
    } catch (err) { handleError(res, err); }
  }));

  router.delete(["/domains/:ownerUserId/:domainId", "/domains/:domainId"], asyncHandler(async (req: Request, res: Response) => {
    if (!domainRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const resolved = resolveDomainParams(req);
      if (!requireOwner(res, currentUserId, resolved.ownerUserId)) return;

      const row = await domainRepo.archive(resolved.ownerUserId, resolved.domainId);
      if (!row) { res.status(404).json({ error: "Domain not found" }); return; }
      res.json(domainRowToApi(row));
    } catch (err) { handleError(res, err); }
  }));

  // ── Templates (global) ──

  router.get("/templates", asyncHandler(async (req: Request, res: Response) => {
    if (!templateRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const activeDomainKeys = domainRepo
        ? new Set((await domainRepo.listAll(currentUserId)).map((d) => `${dbText(d.owner_user_id)}:${dbText(d.domain_id)}`))
        : null;
      const rows = await templateRepo.listAll({ ownerUserId: currentUserId, status: req.query.status as string | undefined });
      const enriched = await Promise.all(
        rows.filter((t) => !activeDomainKeys || activeDomainKeys.has(`${dbText(t.owner_user_id)}:${dbText(t.domain_id)}`)).map(async (t) => {
          const versions = versionRepo ? await versionRepo.listByOwnerDomainAndName(dbText(t.owner_user_id), dbText(t.domain_id), dbText(t.template_name)) : [];
          return templateRowToApi(t, versions);
        }),
      );
      res.json(enriched);
    } catch (err) { handleError(res, err); }
  }));

  // ── Templates by Domain ──

  router.get(["/domains/:ownerUserId/:domainId/templates", "/domains/:domainId/templates"], asyncHandler(async (req: Request, res: Response) => {
    if (!templateRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const resolved = resolveDomainParams(req);
      const rows = await templateRepo.listAll({
        ownerUserId: resolved.ownerUserId,
        domainId: resolved.domainId,
        status: req.query.status as string | undefined,
        includeArchived: String(req.query.includeArchived ?? "").toLowerCase() === "true",
      });
      const enriched = await Promise.all(
        rows.map(async (t) => {
          const versions = versionRepo ? await versionRepo.listByOwnerDomainAndName(dbText(t.owner_user_id), dbText(t.domain_id), dbText(t.template_name)) : [];
          return templateRowToApi(t, versions);
        }),
      );
      res.json(enriched);
    } catch (err) { handleError(res, err); }
  }));

  router.get(["/domains/:ownerUserId/:domainId/templates/:templateName", "/domains/:domainId/templates/:templateName"], asyncHandler(async (req: Request, res: Response) => {
    if (!templateRepo || !versionRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const resolved = resolveDomainParams(req);
      const templateName = decodeURIComponent(String(req.params.templateName));
      const row = await templateRepo.findByOwnerDomainAndName(resolved.ownerUserId, resolved.domainId, templateName);
      if (!row) { res.status(404).json({ error: "Template not found" }); return; }
      if (row.status === "archived" && String(req.query.includeArchived ?? "").toLowerCase() !== "true") {
        res.status(404).json({ error: "Template not found" }); return;
      }
      const versions = await versionRepo.listByOwnerDomainAndName(dbText(row.owner_user_id), dbText(row.domain_id), dbText(row.template_name));
      res.json(templateRowToApi(row, versions));
    } catch (err) { handleError(res, err); }
  }));

  router.post(["/domains/:ownerUserId/:domainId/templates", "/domains/:domainId/templates"], asyncHandler(async (req: Request, res: Response) => {
    if (!templateRepo || !versionRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const resolved = resolveDomainParams(req);
      if (!requireOwner(res, currentUserId, resolved.ownerUserId)) return;

      const { templateName, displayName, description, category, targetType, gradingType, contentMd, sourcePath, sourceHash, status } = req.body;
      if (!templateName || !contentMd) { res.status(400).json({ error: "Missing templateName or contentMd" }); return; }

      const parsed = parseBenchMarkdown(String(contentMd));
      const result = await upsertTemplateAndVersion({
        domainRepo, templateRepo, versionRepo,
        ownerUserId: currentUserId,
        domainId: resolved.domainId, templateName: String(templateName),
        displayName: displayName ?? String(templateName),
        description, category, targetType, gradingType,
        contentMd: String(contentMd), parsedMetaJson: JSON.stringify(parsed),
        sourcePath: sourcePath ?? null, sourceHash: sourceHash ?? null,
        status: status ?? "draft",
      });
      res.status(201).json(result);
    } catch (err) { handleError(res, err); }
  }));

  router.put(["/domains/:ownerUserId/:domainId/templates/:templateName", "/domains/:domainId/templates/:templateName"], asyncHandler(async (req: Request, res: Response) => {
    if (!templateRepo || !versionRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const resolved = resolveDomainParams(req);
      if (!requireOwner(res, currentUserId, resolved.ownerUserId)) return;

      const templateName = decodeURIComponent(String(req.params.templateName));
      const { displayName, description, category, targetType, gradingType, contentMd, sourcePath, sourceHash, status } = req.body;
      const parsed = contentMd ? parseBenchMarkdown(String(contentMd)) : null;
      const result = await upsertTemplateAndVersion({
        domainRepo, templateRepo, versionRepo,
        ownerUserId: currentUserId,
        domainId: resolved.domainId, templateName,
        displayName, description, category, targetType, gradingType,
        contentMd: contentMd ? String(contentMd) : undefined,
        parsedMetaJson: parsed ? JSON.stringify(parsed) : undefined,
        sourcePath: sourcePath ?? null, sourceHash: sourceHash ?? null,
        status: status ?? "draft",
      });
      res.json(result);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("published")) {
        res.status(409).json({ error: "Conflict", message: msg });
        return;
      }
      handleError(res, err);
    }
  }));

  router.delete(["/domains/:ownerUserId/:domainId/templates/:templateName", "/domains/:domainId/templates/:templateName"], asyncHandler(async (req: Request, res: Response) => {
    if (!templateRepo || !versionRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const resolved = resolveDomainParams(req);
      if (!requireOwner(res, currentUserId, resolved.ownerUserId)) return;

      const templateName = decodeURIComponent(String(req.params.templateName));
      const row = await templateRepo.update(resolved.ownerUserId, resolved.domainId, templateName, { status: "archived" });
      if (!row) { res.status(404).json({ error: "Template not found" }); return; }
      const versions = await versionRepo.listByOwnerDomainAndName(dbText(row.owner_user_id), dbText(row.domain_id), dbText(row.template_name));
      res.json(templateRowToApi(row, versions));
    } catch (err) { handleError(res, err); }
  }));

  router.post(["/domains/:ownerUserId/:domainId/templates/:templateName/publish", "/domains/:domainId/templates/:templateName/publish"], asyncHandler(async (req: Request, res: Response) => {
    if (!templateRepo || !versionRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const resolved = resolveDomainParams(req);
      if (!requireOwner(res, currentUserId, resolved.ownerUserId)) return;

      const templateName = decodeURIComponent(String(req.params.templateName));
      const version = req.query.version ? Number(req.query.version) : undefined;

      const template = await templateRepo.findByOwnerDomainAndName(resolved.ownerUserId, resolved.domainId, templateName);
      if (!template) { res.status(404).json({ error: "Template not found" }); return; }

      let targetVersion = version;
      if (targetVersion === undefined) {
        const draft = await versionRepo.findDraftVersionByOwner(resolved.ownerUserId, resolved.domainId, templateName);
        if (!draft) { res.status(404).json({ error: "No draft version to publish" }); return; }
        targetVersion = draft.version;
      }

      await publishTemplateVersion({
        templateRepo,
        versionRepo,
        ownerUserId: resolved.ownerUserId,
        domainId: resolved.domainId,
        templateName,
        version: targetVersion,
      });

      const versions = await versionRepo.listByOwnerDomainAndName(resolved.ownerUserId, resolved.domainId, templateName);
      res.json(templateRowToApi({ ...template, published_version: targetVersion, status: "published" }, versions));
    } catch (err) {
      if (err instanceof Error && err.name === "TemplateValidationError") {
        res.status(400).json({
          error: "Template validation failed",
          templateName: decodeURIComponent(String(req.params.templateName)),
          validator_error_message: err.message,
        });
        return;
      }
      handleError(res, err);
    }
  }));

  router.post(["/domains/:ownerUserId/:domainId/templates/batch-publish", "/domains/:domainId/templates/batch-publish"], asyncHandler(async (req: Request, res: Response) => {
    if (!templateRepo || !versionRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const resolved = resolveDomainParams(req);
      if (!requireOwner(res, currentUserId, resolved.ownerUserId)) return;

      const templates = Array.isArray(req.body?.templates) ? req.body.templates : [];
      if (templates.length === 0) {
        res.status(400).json({ error: "Missing templates array" }); return;
      }
      if (templates.length > 500) {
        res.status(400).json({ error: "Too many templates", message: "At most 500 templates can be published in one request" }); return;
      }

      const items = [];
      for (const item of templates) {
        const templateName = String((item as Record<string, unknown>).templateName ?? "");
        const versionRaw = (item as Record<string, unknown>).version;
        const version = versionRaw === undefined || versionRaw === null ? undefined : Number(versionRaw);
        if (!templateName) {
          items.push({ templateName, version: version ?? null, success: false, skipped: false, reason: "Missing templateName" });
          continue;
        }

        try {
          const result = await publishTemplateVersion({
            templateRepo,
            versionRepo,
            ownerUserId: resolved.ownerUserId,
            domainId: resolved.domainId,
            templateName,
            version,
          });
          items.push({
            templateName,
            version: result.version,
            success: true,
            skipped: false,
            reason: "",
          });
        } catch (err) {
          const message = err instanceof Error ? err.message : String(err);
          const isValidationError = err instanceof Error && err.name === "TemplateValidationError";
          items.push({
            templateName,
            version: version ?? null,
            success: false,
            skipped: false,
            reason: message,
            validator_error_message: isValidationError ? message : undefined,
          });
        }
      }

      res.json({
        published: items.filter((item) => item.success).length,
        failed: items.filter((item) => !item.success).length,
        items,
      });
    } catch (err) { handleError(res, err); }
  }));

  // ── Uploads ──

  router.post(
    ["/domains/:ownerUserId/:domainId/uploads/scan", "/domains/:domainId/uploads/scan"],
    upload.array("files"),
    asyncHandler(async (req: Request, res: Response) => {
      if (!templateRepo || !versionRepo || !db) { res.status(503).json({ error: "Service Unavailable" }); return; }
      try {
        const currentUserId = getCurrentUserId(req);
        if (!requireAuth(res, currentUserId)) return;

        const resolved = resolveDomainParams(req);
        if (!requireOwner(res, currentUserId, resolved.ownerUserId)) return;

        const files = req.files as Express.Multer.File[] | undefined;
        if (!files || files.length === 0) {
          res.status(400).json({ error: "No files uploaded" }); return;
        }

        const parsedFiles = await parseUploadedFiles(files);
        const scanResult = await scanUploadedFiles({
          ownerUserId: currentUserId,
          domainId: resolved.domainId,
          parsedFiles,
          templateRepo,
          versionRepo,
        });
        const importResult = await importScannedFiles({
          domainRepo,
          templateRepo,
          versionRepo,
          db,
          ownerUserId: currentUserId,
          domainId: resolved.domainId,
          parsedFiles,
          scanItems: scanResult.items,
        });

        res.json(importResult);
      } catch (err) { handleError(res, err); }
    },
  ));

  // ── Runs ──

  router.post("/runs", asyncHandler(async (req: Request, res: Response) => {
    if (!runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const { ownerId, domainId, templateName, templateVersion, targetType, status, model, suite, scene, triggeredBy, clawmindFlowId, sessionId, sessionKey, runConfig, startedAt } = req.body;
      if (!domainId || !templateName || templateVersion === undefined) {
        res.status(400).json({ error: "Missing domainId, templateName, or templateVersion" }); return;
      }
      const currentUserId = getCurrentUserId(req);
      // Workflow callers pass the frozen template owner explicitly. Keep the
      // legacy header/domain-ref fallback for existing Bench clients only.
      const resolved = ownerId != null && String(ownerId).trim()
        ? { ownerUserId: String(ownerId).trim(), domainId: String(domainId) }
        : resolveDomainRef(String(domainId), currentUserId);
      const benchRunId = generateBenchRunId();
      const detailUrl = `${getBaseUrl(req)}/bench/runs/${benchRunId}`;

      // Normalize runConfig with runScope for both template and domain runs
      const normalizedRunConfig: Record<string, unknown> = runConfig !== undefined && runConfig !== null
        ? (typeof runConfig === "string" ? JSON.parse(runConfig) : { ...runConfig })
        : {};
      const isDomainRun = String(templateName) === "__domain__" && Number(templateVersion) === 0;
      if (!normalizedRunConfig.runScope) {
        normalizedRunConfig.runScope = isDomainRun ? "domain" : "template";
      }
      if (isDomainRun && !normalizedRunConfig.templateCount) {
        normalizedRunConfig.templateCount = 0;
      }
      const normalizedClawmindFlowId = pickClawmindFlowId(clawmindFlowId, normalizedRunConfig);
      if (normalizedClawmindFlowId && !normalizedRunConfig.clawmindFlowId) {
        normalizedRunConfig.clawmindFlowId = normalizedClawmindFlowId;
      }

      await runRepo.create({
        benchRunId,
        domainId: resolved.domainId,
        templateName: String(templateName),
        templateVersion: Number(templateVersion),
        targetType: targetType !== undefined ? String(targetType) : undefined,
        status: status !== undefined ? String(status) : undefined,
        model: model !== undefined ? String(model) : null,
        suite: suite !== undefined ? String(suite) : null,
        scene: scene !== undefined ? String(scene) : null,
        triggeredBy: triggeredBy !== undefined ? String(triggeredBy) : null,
        clawmindFlowId: normalizedClawmindFlowId,
        sessionId: sessionId !== undefined ? String(sessionId) : null,
        sessionKey: sessionKey !== undefined ? String(sessionKey) : null,
        runConfigJson: JSON.stringify(normalizedRunConfig),
        startedAt: startedAt !== undefined ? Number(startedAt) : undefined,
        ownerUserId: resolved.ownerUserId,
      });
      res.status(201).json({ benchRunId, detailUrl });
    } catch (err) { handleError(res, err); }
  }));

  router.put("/runs/:benchRunId", asyncHandler(async (req: Request, res: Response) => {
    if (!runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const benchRunId = String(req.params.benchRunId);
      const { status, score, maxScore, passRate, summary, errorText, completedAt, startedAt, clawmindFlowId, sessionId, sessionKey } = req.body;
      const row = await runRepo.update(benchRunId, {
        status: status !== undefined ? String(status) : undefined,
        score: score !== undefined ? Number(score) : undefined,
        maxScore: maxScore !== undefined ? Number(maxScore) : undefined,
        passRate: passRate !== undefined ? Number(passRate) : undefined,
        summaryJson: summary !== undefined ? JSON.stringify(summary) : undefined,
        errorText: errorText !== undefined ? String(errorText) : undefined,
        startedAt: startedAt !== undefined ? Number(startedAt) : undefined,
        completedAt: completedAt !== undefined ? Number(completedAt) : undefined,
        clawmindFlowId: clawmindFlowId !== undefined ? String(clawmindFlowId) : undefined,
        sessionId: sessionId !== undefined ? String(sessionId) : undefined,
        sessionKey: sessionKey !== undefined ? String(sessionKey) : undefined,
      });
      if (!row) { res.status(404).json({ error: "Run not found" }); return; }
      res.json(runRowToApi(row));
    } catch (err) { handleError(res, err); }
  }));

  router.get("/runs/:benchRunId", asyncHandler(async (req: Request, res: Response) => {
    if (!runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const row = await runRepo.findByBenchRunId(String(req.params.benchRunId));
      if (!row) { res.status(404).json({ error: "Run not found" }); return; }
      res.json(runRowToApi(row));
    } catch (err) { handleError(res, err); }
  }));

  router.get("/runs", asyncHandler(async (req: Request, res: Response) => {
    if (!runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const ownerUserId = req.query.ownerUserId ? String(req.query.ownerUserId) : currentUserId;
      if (currentUserId !== ownerUserId && !req.isAdmin && !req.isBenchAdmin) {
        res.status(403).json({ error: "Forbidden", message: "You can only view your own runs" });
        return;
      }
      const filters = {
        ownerUserId,
        domainId: req.query.domainId ? String(req.query.domainId) : undefined,
        templateName: req.query.templateName ? String(req.query.templateName) : undefined,
        status: req.query.status ? String(req.query.status) : undefined,
        model: req.query.model ? String(req.query.model) : undefined,
        suite: req.query.suite ? String(req.query.suite) : undefined,
        scene: req.query.scene ? String(req.query.scene) : undefined,
        startedFrom: req.query.startedFrom ? Number(req.query.startedFrom) : undefined,
        startedTo: req.query.startedTo ? Number(req.query.startedTo) : undefined,
        limit: req.query.limit ? Math.min(Number(req.query.limit), 200) : 50,
        offset: req.query.offset ? Number(req.query.offset) : 0,
      };
      const [rows, total] = await Promise.all([
        runRepo.listAll(filters),
        runRepo.count(filters),
      ]);
      res.json({
        runs: rows.map(runRowToApi),
        total,
        limit: filters.limit,
        offset: filters.offset,
      });
    } catch (err) { handleError(res, err); }
  }));

  router.get(["/domains/:ownerUserId/:domainId/summary", "/domains/:domainId/summary"], asyncHandler(async (req: Request, res: Response) => {
    if (!domainRepo || !templateRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const resolved = resolveDomainParams(req);
      const domain = await domainRepo.findByOwnerAndDomainId(resolved.ownerUserId, resolved.domainId);
      if (!domain) { res.status(404).json({ error: "Domain not found" }); return; }

      const [templateCount, runCount, latestRun] = await Promise.all([
        templateRepo.countByOwnerAndDomain(resolved.ownerUserId, resolved.domainId),
        runRepo.count({ domainId: resolved.domainId, ownerUserId: resolved.ownerUserId }),
        runRepo.findLatestByOwnerAndDomain(resolved.ownerUserId, resolved.domainId),
      ]);

      res.json({
        domainId: resolved.domainId,
        ownerUserId: resolved.ownerUserId,
        templateCount,
        runCount,
        latestRun: latestRun ? runRowToApi(latestRun) : null,
        latestScore: latestRun?.score ?? null,
        latestPassRate: latestRun?.pass_rate ?? null,
      });
    } catch (err) { handleError(res, err); }
  }));

  router.get(["/domains/:ownerUserId/:domainId/templates/:templateName/runs", "/domains/:domainId/templates/:templateName/runs"], asyncHandler(async (req: Request, res: Response) => {
    if (!runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const resolved = resolveDomainParams(req);
      const templateName = decodeURIComponent(String(req.params.templateName));
      const rows = await runRepo.listAll({
        ownerUserId: resolved.ownerUserId,
        domainId: resolved.domainId,
        limit: 200,
      });
      res.json(rows.filter((row) => runIncludesTemplate(row, templateName)).slice(0, 50).map(runRowToApi));
    } catch (err) { handleError(res, err); }
  }));

  // ── Results ──

  router.post("/runs/:benchRunId/results", asyncHandler(async (req: Request, res: Response) => {
    if (!resultRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const benchRunId = String(req.params.benchRunId);
      const { results } = req.body;
      if (!Array.isArray(results) || results.length === 0) {
        res.status(400).json({ error: "Missing results array" }); return;
      }
      const run = await runRepo.findByBenchRunId(benchRunId);
      if (!run) { res.status(404).json({ error: "Run not found" }); return; }

      const created = await resultRepo.batchCreate(
        results.map((r: Record<string, unknown>, idx: number) => ({
          resultId: String(r.resultId ?? generateResultId(benchRunId, String(r.taskId ?? "task"), idx)),
          benchRunId,
          taskId: String(r.taskId ?? "unknown"),
          taskName: r.taskName !== undefined ? String(r.taskName) : null,
          status: String(r.status ?? "pending"),
          score: r.score !== undefined ? Number(r.score) : null,
          maxScore: r.maxScore !== undefined ? Number(r.maxScore) : null,
          gradingType: r.gradingType !== undefined ? String(r.gradingType) : null,
          executionTimeMs: r.executionTimeMs !== undefined ? Number(r.executionTimeMs) : null,
          transcriptPath: r.transcriptPath !== undefined ? String(r.transcriptPath) : null,
          workspacePath: r.workspacePath !== undefined ? String(r.workspacePath) : null,
          resultJson: r.resultJson !== undefined ? JSON.stringify(r.resultJson) : null,
          breakdownJson: r.breakdown !== undefined ? JSON.stringify(r.breakdown) : null,
          notes: r.notes !== undefined ? String(r.notes) : null,
          errorText: r.errorText !== undefined ? String(r.errorText) : null,
        })),
      );
      res.status(201).json({ created: created.length, results: created.map(resultRowToApi) });
    } catch (err) { handleError(res, err); }
  }));

  router.get("/runs/:benchRunId/results", asyncHandler(async (req: Request, res: Response) => {
    if (!resultRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const rows = await resultRepo.listByBenchRunId(String(req.params.benchRunId));
      res.json(rows.map(resultRowToApi));
    } catch (err) { handleError(res, err); }
  }));

  // ── Artifacts / Sessions ──

  router.post("/runs/:benchRunId/artifacts", asyncHandler(async (req: Request, res: Response) => {
    if (!artifactRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;

      const benchRunId = String(req.params.benchRunId);
      const run = await runRepo.findByBenchRunId(benchRunId);
      if (!run) { res.status(404).json({ error: "Run not found" }); return; }
      if (!requireOwner(res, currentUserId, dbText(run.owner_user_id))) return;

      const {
        artifactType,
        taskId,
        resultId,
        filename,
        contentType,
        contentText,
        contentJson,
        summary,
        summaryJson,
        createdBy,
      } = req.body as Record<string, unknown>;

      if (!artifactType) {
        res.status(400).json({ error: "Missing artifactType" }); return;
      }
      if (contentText !== undefined && typeof contentText !== "string") {
        res.status(400).json({ error: "Invalid contentText", message: "contentText must be a string" }); return;
      }
      if (contentJson !== undefined) {
        try {
          JSON.stringify(contentJson);
        } catch {
          res.status(400).json({ error: "Invalid contentJson", message: "contentJson must be JSON serializable" }); return;
        }
      }

      const normalizedContentText = typeof contentText === "string" ? contentText : null;
      const normalizedContentJson = contentJson !== undefined
        ? JSON.stringify(contentJson)
        : null;
      if ((normalizedContentText === null || normalizedContentText.length === 0) && (normalizedContentJson === null || normalizedContentJson.length === 0)) {
        res.status(400).json({ error: "Missing contentText or contentJson", message: "At least one non-empty artifact content field is required" }); return;
      }
      const normalizedSummaryJson = summaryJson !== undefined
        ? JSON.stringify(summaryJson)
        : summary !== undefined
          ? JSON.stringify(summary)
          : summarizeArtifactContent(String(artifactType), normalizedContentText, normalizedContentJson);
      const contentForHash = normalizedContentText ?? normalizedContentJson ?? "";
      const sizeBytes = Buffer.byteLength(contentForHash, "utf8");
      if (sizeBytes > MAX_ARTIFACT_BYTES) {
        res.status(413).json({ error: "Artifact too large", maxBytes: MAX_ARTIFACT_BYTES, sizeBytes }); return;
      }
      const sha256 = createHash("sha256").update(contentForHash).digest("hex");
      const existingArtifacts = await artifactRepo.listByBenchRunId({
        benchRunId,
        artifactType: String(artifactType),
        taskId: taskId !== undefined && taskId !== null ? String(taskId) : undefined,
        includeContent: false,
      });
      const existingArtifact = existingArtifacts.find((artifact) =>
        dbNullableText(artifact.filename) === (filename !== undefined && filename !== null ? String(filename) : null)
        && dbNullableText(artifact.sha256) === sha256
      );
      if (existingArtifact) {
        res.status(200).json(artifactRowToApi(existingArtifact, false));
        return;
      }

      const row = await artifactRepo.create({
        artifactId: generateArtifactId(benchRunId, String(artifactType)),
        benchRunId,
        resultId: resultId !== undefined && resultId !== null ? String(resultId) : null,
        taskId: taskId !== undefined && taskId !== null ? String(taskId) : null,
        artifactType: String(artifactType),
        filename: filename !== undefined && filename !== null ? String(filename) : null,
        contentType: contentType !== undefined && contentType !== null ? String(contentType) : inferArtifactContentType(String(artifactType), filename),
        sizeBytes,
        storageType: "db",
        contentText: normalizedContentText,
        contentJson: normalizedContentJson,
        summaryJson: normalizedSummaryJson,
        sha256,
        createdBy: createdBy !== undefined && createdBy !== null ? String(createdBy) : currentUserId,
        ownerUserId: currentUserId,
      });
      res.status(201).json(artifactRowToApi(row, false));
    } catch (err) { handleError(res, err); }
  }));

  router.get("/runs/:benchRunId/artifacts", asyncHandler(async (req: Request, res: Response) => {
    if (!artifactRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const benchRunId = String(req.params.benchRunId);
      const run = await runRepo.findByBenchRunId(benchRunId);
      if (!run) { res.status(404).json({ error: "Run not found" }); return; }
      if (!requireOwner(res, currentUserId, dbText(run.owner_user_id))) return;

      const rows = await artifactRepo.listByBenchRunId({
        benchRunId,
        artifactType: req.query.artifactType ? String(req.query.artifactType) : undefined,
        taskId: req.query.taskId ? String(req.query.taskId) : undefined,
        includeContent: String(req.query.includeContent ?? "").toLowerCase() === "true",
      });
      res.json(rows.map((row) => artifactRowToApi(row, String(req.query.includeContent ?? "").toLowerCase() === "true")));
    } catch (err) { handleError(res, err); }
  }));

  router.get("/runs/:benchRunId/sessions", asyncHandler(async (req: Request, res: Response) => {
    if (!artifactRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const benchRunId = String(req.params.benchRunId);
      const run = await runRepo.findByBenchRunId(benchRunId);
      if (!run) { res.status(404).json({ error: "Run not found" }); return; }

      const rows = await artifactRepo.listByBenchRunId({ benchRunId, includeContent: false });
      const sessions = rows
        .filter((row) => isSessionArtifact(dbText(row.artifact_type)))
        .map((row) => sessionArtifactToSummary(row));
      res.json({ benchRunId, sessions });
    } catch (err) { handleError(res, err); }
  }));

  router.get("/runs/:benchRunId/sessions/artifacts/:artifactId", asyncHandler(async (req: Request, res: Response) => {
    if (!artifactRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const benchRunId = String(req.params.benchRunId);
      const artifactId = String(req.params.artifactId);
      const run = await runRepo.findByBenchRunId(benchRunId);
      if (!run) { res.status(404).json({ error: "Run not found" }); return; }

      const artifact = await artifactRepo.findByArtifactId(artifactId);
      if (!artifact || dbText(artifact.bench_run_id) !== benchRunId || !isSessionArtifact(dbText(artifact.artifact_type))) {
        res.status(404).json({ error: "Session not found" }); return;
      }
      res.json(sessionArtifactToDetail(artifact));
    } catch (err) { handleError(res, err); }
  }));

  router.get("/runs/:benchRunId/sessions/:taskId", asyncHandler(async (req: Request, res: Response) => {
    if (!artifactRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const benchRunId = String(req.params.benchRunId);
      const taskId = decodeURIComponent(String(req.params.taskId));
      const run = await runRepo.findByBenchRunId(benchRunId);
      if (!run) { res.status(404).json({ error: "Run not found" }); return; }

      const rows = await artifactRepo.listByBenchRunId({ benchRunId, taskId, includeContent: true });
      const session = rows.find((row) => isSessionArtifact(dbText(row.artifact_type)));
      if (!session) { res.status(404).json({ error: "Session not found" }); return; }
      res.json(sessionArtifactToDetail(session));
    } catch (err) { handleError(res, err); }
  }));

  router.get("/artifacts/:artifactId", asyncHandler(async (req: Request, res: Response) => {
    if (!artifactRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const row = await artifactRepo.findByArtifactId(String(req.params.artifactId));
      if (!row) { res.status(404).json({ error: "Artifact not found" }); return; }
      const run = await runRepo.findByBenchRunId(dbText(row.bench_run_id));
      if (!run) { res.status(404).json({ error: "Run not found" }); return; }
      if (!requireOwner(res, currentUserId, dbText(run.owner_user_id))) return;
      res.json(artifactRowToApi(row, false));
    } catch (err) { handleError(res, err); }
  }));

  router.get("/artifacts/:artifactId/content", asyncHandler(async (req: Request, res: Response) => {
    if (!artifactRepo || !runRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    try {
      const currentUserId = getCurrentUserId(req);
      if (!requireAuth(res, currentUserId)) return;
      const row = await artifactRepo.findByArtifactId(String(req.params.artifactId));
      if (!row) { res.status(404).json({ error: "Artifact not found" }); return; }
      const run = await runRepo.findByBenchRunId(dbText(row.bench_run_id));
      if (!run) { res.status(404).json({ error: "Run not found" }); return; }
      if (!requireOwner(res, currentUserId, dbText(run.owner_user_id))) return;

      if (row.content_json) {
        res.type(row.content_type ?? "application/json").send(row.content_json);
        return;
      }
      res.type(row.content_type ?? "text/plain").send(row.content_text ?? "");
    } catch (err) { handleError(res, err); }
  }));

  return router;
}

// ── Helpers ──

function handleError(res: Response, err: unknown) {
  const msg = err instanceof Error ? err.message : String(err);
  if (msg.includes("Unauthorized")) {
    res.status(401).json({ error: "Unauthorized", message: msg });
    return;
  }
  if (msg.includes("Forbidden")) {
    res.status(403).json({ error: "Forbidden", message: msg });
    return;
  }
  res.status(500).json({ error: "Internal Server Error", message: msg });
}

function generateBenchRunId(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  const h = String(now.getHours()).padStart(2, "0");
  const min = String(now.getMinutes()).padStart(2, "0");
  const s = String(now.getSeconds()).padStart(2, "0");
  const rand = Math.random().toString(36).slice(2, 8);
  return `bench_${y}${m}${d}_${h}${min}${s}_${rand}`;
}

function generateResultId(benchRunId: string, taskId: string, index: number): string {
  const hash = createHash("sha256").update(`${benchRunId}:${taskId}:${index}:${Math.random()}`).digest("hex").slice(0, 8);
  return `res_${hash}`;
}

function generateArtifactId(benchRunId: string, artifactType: string): string {
  const hash = createHash("sha256").update(`${benchRunId}:${artifactType}:${Date.now()}:${Math.random()}`).digest("hex").slice(0, 12);
  return `artifact_${hash}`;
}

function getBaseUrl(req: Request): string {
  const host = req.get("host") ?? "localhost:3001";
  const protocol = req.get("x-forwarded-proto") ?? (req.secure ? "https" : "http");
  return `${protocol}://${host}`;
}

function hashContent(content: string): string {
  return createHash("sha256").update(content).digest("hex").slice(0, 16);
}

type AdminQuery = Record<string, unknown>;

function buildAdminRunFilter(query: AdminQuery): { join: string; where: string; values: unknown[] } {
  const joins: string[] = [];
  const conditions: string[] = [];
  const values: unknown[] = [];
  const tagId = getQueryText(query.tagId);
  if (tagId) {
    joins.push(
      `JOIN cm_bench_domain_tags dt
       ON dt.owner_user_id = r.owner_user_id
      AND dt.domain_id = r.domain_id
      AND dt.tag_id = ?`,
    );
    values.push(tagId);
  }
  const ownerUserId = getQueryText(query.ownerUserId);
  if (ownerUserId) {
    conditions.push("r.owner_user_id = ?");
    values.push(ownerUserId);
  }
  const domainId = getQueryText(query.domainId);
  if (domainId) {
    conditions.push("r.domain_id = ?");
    values.push(domainId);
  }
  const templateName = getQueryText(query.templateName);
  if (templateName) {
    conditions.push("r.template_name LIKE ?");
    values.push(`%${templateName}%`);
  }
  const status = getQueryText(query.status);
  if (status && status !== "all") {
    conditions.push("r.status = ?");
    values.push(status);
  }
  const startedFrom = getQueryNumber(query.startedFrom ?? query.from);
  if (startedFrom !== undefined) {
    conditions.push("r.started_at >= ?");
    values.push(normalizeEpochSeconds(startedFrom));
  }
  const startedTo = getQueryNumber(query.startedTo ?? query.to);
  if (startedTo !== undefined) {
    conditions.push("r.started_at <= ?");
    values.push(normalizeEpochSeconds(startedTo));
  }
  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
  const join = joins.join(" ");
  return { join, where, values };
}

function buildAdminTemplateFilter(query: AdminQuery, options: { includeStatus?: boolean } = {}): { join: string; where: string; values: unknown[]; joinValues: unknown[]; whereValues: unknown[] } {
  const joins: string[] = [];
  const conditions: string[] = [];
  const joinValues: unknown[] = [];
  const whereValues: unknown[] = [];
  const tagId = getQueryText(query.tagId);
  if (tagId) {
    joins.push(
      `JOIN cm_bench_domain_tags dt
       ON dt.owner_user_id = t.owner_user_id
      AND dt.domain_id = t.domain_id
      AND dt.tag_id = ?`,
    );
    joinValues.push(tagId);
  }
  const ownerUserId = getQueryText(query.ownerUserId);
  if (ownerUserId) {
    conditions.push("t.owner_user_id = ?");
    whereValues.push(ownerUserId);
  }
  const domainId = getQueryText(query.domainId);
  if (domainId) {
    conditions.push("t.domain_id = ?");
    whereValues.push(domainId);
  }
  const templateName = getQueryText(query.templateName);
  if (templateName) {
    conditions.push("t.template_name LIKE ?");
    whereValues.push(`%${templateName}%`);
  }
  const status = getQueryText(query.status);
  if (options.includeStatus && status && status !== "all") {
    conditions.push("t.status = ?");
    whereValues.push(status);
  }
  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
  const values = [...joinValues, ...whereValues];
  return { join: joins.join(" "), where, values, joinValues, whereValues };
}

function buildAdminRunJoinFilter(query: AdminQuery): { on: string; values: unknown[] } {
  const conditions = [
    "r.owner_user_id = t.owner_user_id",
    "r.domain_id = t.domain_id",
    "r.template_name = t.template_name",
  ];
  const values: unknown[] = [];
  const status = getQueryText(query.status);
  if (status && status !== "all") {
    conditions.push("r.status = ?");
    values.push(status);
  }
  const startedFrom = getQueryNumber(query.startedFrom ?? query.from);
  if (startedFrom !== undefined) {
    conditions.push("r.started_at >= ?");
    values.push(normalizeEpochSeconds(startedFrom));
  }
  const startedTo = getQueryNumber(query.startedTo ?? query.to);
  if (startedTo !== undefined) {
    conditions.push("r.started_at <= ?");
    values.push(normalizeEpochSeconds(startedTo));
  }
  return { on: conditions.join(" AND "), values };
}

function getQueryText(value: unknown): string | undefined {
  if (Array.isArray(value)) return getQueryText(value[0]);
  if (value === null || value === undefined) return undefined;
  const text = String(value).trim();
  return text || undefined;
}

function getQueryNumber(value: unknown): number | undefined {
  const text = getQueryText(value);
  if (!text) return undefined;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function normalizeEpochSeconds(value: number): number {
  return value > 1e12 ? Math.floor(value / 1000) : Math.floor(value);
}

function parseVersionMode(value: unknown): "published" | "latest" | "all_versions" {
  return value === "latest" || value === "all_versions" ? value : "published";
}

function normalizeTagId(value: string): string {
  return value.trim().replace(/\s+/g, "_").replace(/[^\p{L}\p{N}_.:-]/gu, "_").slice(0, 128);
}

function normalizeTagIds(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  return Array.from(new Set(values.map((value) => normalizeTagId(String(value))).filter(Boolean)));
}

function normalizeDomainKeys(values: unknown): DomainKey[] {
  if (!Array.isArray(values)) return [];
  const keys = values
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const row = item as Record<string, unknown>;
      const ownerUserId = getQueryText(row.ownerUserId);
      const domainId = getQueryText(row.domainId);
      if (!ownerUserId || !domainId) return null;
      return { ownerUserId, domainId };
    })
    .filter((item): item is DomainKey => !!item);
  const deduped = new Map<string, DomainKey>();
  for (const key of keys) {
    deduped.set(domainKey(key.ownerUserId, key.domainId), key);
  }
  return Array.from(deduped.values());
}

function domainKey(ownerUserId: string, domainId: string): string {
  return `${ownerUserId}\u0000${domainId}`;
}

function tagRowToApi(row: BenchTagRow) {
  return {
    id: row.id,
    tagId: dbText(row.tag_id),
    name: dbText(row.name),
    description: dbNullableText(row.description),
    status: dbText(row.status),
    createdBy: dbNullableText(row.created_by),
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modify,
  };
}

function domainTagRowToApi(row: BenchDomainTagRow) {
  return {
    tagId: dbText(row.tag_id),
    name: dbText(row.name),
    status: dbText(row.status),
  };
}

function firstTagName(tags: ReturnType<typeof domainTagRowToApi>[]): string {
  return tags[0]?.name ?? "default";
}

function compareAdminText(a: string, b: string): number {
  return a.localeCompare(b, "zh-Hans-CN", { numeric: true, sensitivity: "base" });
}

async function safeListTags(repo: BenchTagRepository, includeArchived: boolean): Promise<BenchTagRow[]> {
  try {
    return await repo.listTags(includeArchived);
  } catch (error) {
    if (isMissingBenchTagTable(error)) {
      console.warn("[bench-admin] cm_bench_tags missing; returning empty tag list");
      return [];
    }
    throw error;
  }
}

async function safeListDomainTags(repo: BenchTagRepository | null, keys: DomainKey[]) {
  const byDomain = new Map<string, ReturnType<typeof domainTagRowToApi>[]>();
  if (!repo || keys.length === 0) return byDomain;
  try {
    const rows = await repo.listTagsForDomains(keys);
    for (const row of rows) {
      const key = domainKey(dbText(row.owner_user_id), dbText(row.domain_id));
      const list = byDomain.get(key) ?? [];
      list.push(domainTagRowToApi(row));
      byDomain.set(key, list);
    }
  } catch (error) {
    if (isMissingBenchTagTable(error)) {
      console.warn("[bench-admin] bench tag tables missing; domain tags omitted");
      return byDomain;
    }
    throw error;
  }
  return byDomain;
}

function isMissingBenchTagTable(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes("cm_bench_tags")
    || message.includes("cm_bench_domain_tags")
    || message.includes("no such table")
    || message.includes("doesn't exist")
    || message.includes("not exist");
}

function safePathSegment(value: string): string {
  return value.trim().replace(/[\\/:*?"<>|\u0000-\u001F]/g, "_").replace(/\s+/g, "_") || "unknown";
}

function generateFileKey(entryPath: string, index: number): string {
  return createHash("sha256").update(`${entryPath}:${index}`).digest("hex").slice(0, 16);
}

async function publishTemplateVersion(args: {
  templateRepo: BenchTemplateRepository;
  versionRepo: BenchTemplateVersionRepository;
  ownerUserId: string;
  domainId: string;
  templateName: string;
  version?: number;
}): Promise<{ version: number }> {
  const template = await args.templateRepo.findByOwnerDomainAndName(args.ownerUserId, args.domainId, args.templateName);
  if (!template) throw new Error("Template not found");
  if (template.status === "archived") throw new Error("Template archived");

  let targetVersion = args.version;
  if (targetVersion === undefined) {
    const draft = await args.versionRepo.findDraftVersionByOwner(args.ownerUserId, args.domainId, args.templateName);
    if (!draft) throw new Error("No draft version to publish");
    targetVersion = draft.version;
  }

  const versionRow = await args.versionRepo.findByOwnerDomainNameVersion(args.ownerUserId, args.domainId, args.templateName, targetVersion);
  if (!versionRow) throw new Error("Version not found");
  const validation = validateClawBenchRuntimeTemplate(dbText(versionRow.content_md));
  if (!validation.valid) {
    const error = new Error(validation.validator_error_message ?? "Template validation failed");
    error.name = "TemplateValidationError";
    throw error;
  }

  const published = await args.versionRepo.publishByOwner(args.ownerUserId, args.domainId, args.templateName, targetVersion);
  if (!published) throw new Error("Version not found");

  await args.templateRepo.update(args.ownerUserId, args.domainId, args.templateName, {
    publishedVersion: targetVersion,
    status: "published",
  });
  return { version: targetVersion };
}

async function upsertTemplateAndVersion(args: {
  domainRepo: BenchDomainRepository | null;
  templateRepo: BenchTemplateRepository;
  versionRepo: BenchTemplateVersionRepository;
  ownerUserId: string;
  domainId: string;
  templateName: string;
  displayName?: string | null;
  description?: string | null;
  category?: string | null;
  targetType?: string;
  gradingType?: string;
  contentMd?: string;
  parsedMetaJson?: string;
  sourcePath?: string | null;
  sourceHash?: string | null;
  status?: string;
}): Promise<Record<string, unknown>> {
  const { domainRepo, templateRepo, versionRepo, ownerUserId, domainId, templateName } = args;

  // Ensure domain exists
  if (domainRepo) {
    const domain = await domainRepo.findByOwnerAndDomainId(ownerUserId, domainId);
    if (!domain) {
      await domainRepo.create({ domainId, name: domainId, ownerUserId });
    }
  }

  const existingTemplate = await templateRepo.findByOwnerDomainAndName(ownerUserId, domainId, templateName);
  let version = 1;
  let action = "new";

  if (existingTemplate) {
    const isArchivedTemplate = existingTemplate.status === "archived";
    // Check if content changed by comparing hash
    const currentDraft = await versionRepo.findDraftVersionByOwner(ownerUserId, domainId, templateName);
    if (!isArchivedTemplate && currentDraft && args.sourceHash && currentDraft.source_hash === args.sourceHash) {
      // Content unchanged, return existing
      const versions = await versionRepo.listByOwnerDomainAndName(ownerUserId, domainId, templateName);
      return { action: "skip", ...templateRowToApi(existingTemplate, versions) };
    }

    if (currentDraft) {
      // Update existing draft
      action = "update";
      version = currentDraft.version;
      if (args.contentMd !== undefined) {
        await versionRepo.update(ownerUserId, domainId, templateName, version, {
          displayName: args.displayName,
          description: args.description,
          contentMd: args.contentMd,
          parsedMetaJson: args.parsedMetaJson,
          sourcePath: args.sourcePath,
          sourceHash: args.sourceHash,
        });
      }
    } else {
      // Create new version
      action = "update";
      version = (existingTemplate.latest_version ?? 0) + 1;
      await versionRepo.create({
        domainId,
        templateName,
        version,
        displayName: args.displayName,
        description: args.description,
        contentMd: args.contentMd ?? "",
        parsedMetaJson: args.parsedMetaJson,
        sourcePath: args.sourcePath,
        sourceHash: args.sourceHash,
        status: args.status ?? "draft",
        ownerUserId,
      });
    }

    await templateRepo.update(ownerUserId, domainId, templateName, {
      displayName: args.displayName,
      description: args.description,
      category: args.category,
      targetType: args.targetType,
      gradingType: args.gradingType,
      sourcePath: args.sourcePath,
      sourceHash: args.sourceHash,
      latestVersion: version,
      status: args.status ?? "draft",
    });
  } else {
    // Create new template + version 1
    await templateRepo.create({
      domainId,
      templateName,
      displayName: args.displayName ?? templateName,
      description: args.description,
      category: args.category,
      targetType: args.targetType,
      gradingType: args.gradingType,
      sourcePath: args.sourcePath,
      sourceHash: args.sourceHash,
      latestVersion: 1,
      status: args.status ?? "draft",
      ownerUserId,
    });
    await versionRepo.create({
      domainId,
      templateName,
      version: 1,
      displayName: args.displayName,
      description: args.description,
      contentMd: args.contentMd ?? "",
      parsedMetaJson: args.parsedMetaJson,
      sourcePath: args.sourcePath,
      sourceHash: args.sourceHash,
      status: args.status ?? "draft",
      ownerUserId,
    });
  }

  const template = await templateRepo.findByOwnerDomainAndName(ownerUserId, domainId, templateName);
  if (!template) {
    throw new Error(`Template not found after upsert: ${domainId}/${templateName}`);
  }
  const versions = await versionRepo.listByOwnerDomainAndName(ownerUserId, domainId, templateName);
  return { action, ...templateRowToApi(template, versions) };
}

type ParsedFile = {
  fileKey: string;
  entryPath: string;
  originalFilename: string;
  contentMd: string;
  sourceHash: string;
  templateName: string;
  displayName: string;
  parsedMeta: Record<string, unknown>;
};

type UploadScanAction = "new" | "update" | "skip" | "conflict";

type UploadScanItem = {
  action: UploadScanAction;
  templateName: string;
  displayName: string;
  originalFilename: string;
  entryPath: string;
  fileKey: string;
  currentVersion: number | null;
  nextVersion: number | null;
  sourceHash: string;
  reason: string;
};

async function parseUploadedFiles(
  files: Express.Multer.File[],
): Promise<ParsedFile[]> {
  const results: ParsedFile[] = [];
  const excludeNames = ["README.md", "TASK_TEMPLATE.md"];
  let globalIndex = 0;

  for (const file of files) {
    const originalName = file.originalname;

    if (originalName.endsWith(".md")) {
      if (excludeNames.includes(originalName)) continue;
      const entryPath = originalName;
      const contentMd = file.buffer.toString("utf-8");
      const parsed = parseBenchMarkdown(contentMd);
      const frontmatter = parsed.frontmatter ?? {};
      const filename = originalName.replace(/\.md$/i, "");
      const templateName = String(frontmatter.id ?? frontmatter.taskId ?? frontmatter.task_id ?? filename);
      const displayName = String(frontmatter.name ?? frontmatter.title ?? templateName);
      const sourceHash = hashContent(contentMd);
      const fileKey = generateFileKey(entryPath, globalIndex++);
      const item: ParsedFile = { fileKey, entryPath, originalFilename: originalName, contentMd, sourceHash, templateName, displayName, parsedMeta: parsed as Record<string, unknown> };
      results.push(item);
    } else if (originalName.endsWith(".zip")) {
      const zip = await JSZip.loadAsync(file.buffer);
      for (const [path, zipEntry] of Object.entries(zip.files)) {
        if (zipEntry.dir) continue;
        const name = path.split("/").pop() ?? "";
        if (!name.endsWith(".md")) continue;
        if (excludeNames.includes(name)) continue;
        const entryPath = path;
        const contentMd = await zipEntry.async("string");
        const parsed = parseBenchMarkdown(contentMd);
        const frontmatter = parsed.frontmatter ?? {};
        const filename = name.replace(/\.md$/i, "");
        const templateName = String(frontmatter.id ?? frontmatter.taskId ?? frontmatter.task_id ?? filename);
        const displayName = String(frontmatter.name ?? frontmatter.title ?? templateName);
        const sourceHash = hashContent(contentMd);
        const fileKey = generateFileKey(entryPath, globalIndex++);
        const item: ParsedFile = { fileKey, entryPath, originalFilename: name, contentMd, sourceHash, templateName, displayName, parsedMeta: parsed as Record<string, unknown> };
        results.push(item);
      }
    }
    // Skip other file types
  }

  return results;
}

async function scanUploadedFiles(args: {
  ownerUserId: string;
  domainId: string;
  parsedFiles: ParsedFile[];
  templateRepo: BenchTemplateRepository;
  versionRepo: BenchTemplateVersionRepository;
}): Promise<{
  domainId: string;
  summary: { new: number; update: number; skip: number; conflict: number };
  items: UploadScanItem[];
}> {
  const { ownerUserId, domainId, parsedFiles, templateRepo, versionRepo } = args;

  const items: UploadScanItem[] = [];
  const nameMap = new Map<string, string[]>(); // templateName -> [fileKeys]

  for (const file of parsedFiles) {
    const existing = await templateRepo.findByOwnerDomainAndName(ownerUserId, domainId, file.templateName);
    let action: UploadScanAction = "new";
    let currentVersion: number | null = null;
    let nextVersion: number | null = 1;
    let reason = "";

    if (existing) {
      currentVersion = existing.latest_version;
      const draft = await versionRepo.findDraftVersionByOwner(ownerUserId, domainId, file.templateName);
      if (existing.status === "archived") {
        action = "update";
        reason = "archived template will be restored";
        nextVersion = draft ? draft.version : (existing.latest_version ?? 0) + 1;
      } else if (draft && draft.source_hash === file.sourceHash) {
        action = "skip";
        reason = "content unchanged";
        nextVersion = null;
      } else {
        action = "update";
        reason = "content changed";
        nextVersion = (existing.latest_version ?? 0) + 1;
      }
    }

    if (!nameMap.has(file.templateName)) nameMap.set(file.templateName, []);
    nameMap.get(file.templateName)!.push(file.fileKey);

    items.push({
      action,
      templateName: file.templateName,
      displayName: file.displayName,
      originalFilename: file.originalFilename,
      entryPath: file.entryPath,
      fileKey: file.fileKey,
      currentVersion,
      nextVersion,
      sourceHash: file.sourceHash,
      reason,
    });
  }

  // Detect conflicts: same templateName from different files
  for (const [templateName, fileKeys] of nameMap.entries()) {
    if (fileKeys.length > 1) {
      for (const item of items) {
        if (item.templateName === templateName) {
          item.action = "conflict";
          item.reason = `multiple files resolve to same templateName: ${fileKeys.join(", ")}`;
        }
      }
    }
  }

  const summary = {
    new: items.filter((i) => i.action === "new").length,
    update: items.filter((i) => i.action === "update").length,
    skip: items.filter((i) => i.action === "skip").length,
    conflict: items.filter((i) => i.action === "conflict").length,
  };

  return { domainId, summary, items };
}

async function importScannedFiles(args: {
  domainRepo: BenchDomainRepository | null;
  templateRepo: BenchTemplateRepository;
  versionRepo: BenchTemplateVersionRepository;
  db: import("@avernet/clawweb-shared/server/db").IDatabase;
  ownerUserId: string;
  domainId: string;
  parsedFiles: ParsedFile[];
  scanItems: UploadScanItem[];
}): Promise<Record<string, unknown>> {
  const parsedByKey = new Map(args.parsedFiles.map((file) => [file.fileKey, file]));
  const importedByKey = new Map<string, Record<string, unknown>>();

  await args.db.transaction(async () => {
    for (const item of args.scanItems) {
      const action = String(item.action ?? "");
      if (action === "skip" || action === "conflict") continue;

      const fileKey = String(item.fileKey ?? "");
      const parsed = parsedByKey.get(fileKey);
      if (!parsed) continue;

      const frontmatter = parsed.parsedMeta.frontmatter as Record<string, unknown> | undefined;
      const result = await upsertTemplateAndVersion({
        domainRepo: args.domainRepo,
        templateRepo: args.templateRepo,
        versionRepo: args.versionRepo,
        ownerUserId: args.ownerUserId,
        domainId: args.domainId,
        templateName: parsed.templateName,
        displayName: parsed.displayName,
        description: frontmatter?.description ? String(frontmatter.description) : null,
        category: null,
        targetType: frontmatter?.targetType ? String(frontmatter.targetType) : "agent_session",
        gradingType: frontmatter?.gradingType ? String(frontmatter.gradingType) : "automated",
        contentMd: parsed.contentMd,
        parsedMetaJson: JSON.stringify(parsed.parsedMeta),
        sourcePath: null,
        sourceHash: parsed.sourceHash,
        status: "draft",
      });
      importedByKey.set(fileKey, result);
    }
  });

  const items = args.scanItems.map((item) => {
    const fileKey = String(item.fileKey ?? "");
    const imported = importedByKey.has(fileKey);
    return {
      ...item,
      imported,
      importResult: importedByKey.get(fileKey) ?? null,
    };
  });

  const summary = {
    new: items.filter((i) => i.action === "new").length,
    update: items.filter((i) => i.action === "update").length,
    skip: items.filter((i) => i.action === "skip").length,
    conflict: items.filter((i) => i.action === "conflict").length,
    imported: importedByKey.size,
  };

  return { domainId: args.domainId, summary, items };
}

function domainRowToApi(row: {
  id: number;
  domain_id: unknown;
  name: unknown;
  description: unknown;
  status: unknown;
  created_by: unknown;
  owner_user_id: unknown;
  gmt_create: number;
  gmt_modified: number;
}) {
  return {
    id: row.id,
    domainId: dbText(row.domain_id),
    name: dbText(row.name),
    description: dbNullableText(row.description),
    status: dbText(row.status),
    createdBy: dbNullableText(row.created_by),
    ownerUserId: dbText(row.owner_user_id),
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}

function templateRowToApi(
  row: {
    id: number;
    domain_id: unknown;
    template_name: unknown;
    display_name: unknown;
    description: unknown;
    category: unknown;
    target_type: unknown;
    grading_type: unknown;
    source: unknown;
    source_path: unknown;
    source_hash: unknown;
    latest_version: number;
    published_version: number | null;
    status: unknown;
    created_by: unknown;
    owner_user_id: unknown;
    gmt_create: number;
    gmt_modified: number;
  },
  versions: Array<{
    domain_id: unknown;
    template_name: unknown;
    version: number;
    content_md: unknown;
    status: unknown;
    gmt_create: number;
  }>,
) {
  return {
    id: row.id,
    domainId: dbText(row.domain_id),
    templateName: dbText(row.template_name),
    displayName: dbNullableText(row.display_name),
    description: dbNullableText(row.description),
    category: dbNullableText(row.category),
    targetType: dbText(row.target_type),
    gradingType: dbText(row.grading_type),
    source: dbText(row.source),
    sourcePath: dbNullableText(row.source_path),
    sourceHash: dbNullableText(row.source_hash),
    latestVersion: row.latest_version,
    publishedVersion: row.published_version,
    status: dbText(row.status),
    createdBy: dbNullableText(row.created_by),
    ownerUserId: dbText(row.owner_user_id),
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
    versions: versions.map((v) => ({
      version: v.version,
      status: dbText(v.status),
      contentMd: dbText(v.content_md),
      gmtCreate: v.gmt_create,
    })),
  };
}

function runRowToApi(row: {
  id: number;
  bench_run_id: unknown;
  domain_id: unknown;
  template_name: unknown;
  template_version: number;
  target_type: unknown;
  status: unknown;
  score: number | null;
  max_score: number | null;
  pass_rate: number | null;
  model: unknown;
  suite: unknown;
  scene: unknown;
  triggered_by: unknown;
  clawmind_flow_id: unknown;
  session_id: unknown;
  session_key: unknown;
  run_config_json: unknown;
  summary_json: unknown;
  error_text: unknown;
  started_at: number | null;
  completed_at: number | null;
  owner_user_id: unknown;
  gmt_create: number;
  gmt_modified: number;
}) {
  const templateName = dbText(row.template_name);
  const runConfigJson = dbNullableText(row.run_config_json);
  const summaryJson = dbNullableText(row.summary_json);
  const runConfig = runConfigJson ? parseJsonSafe(runConfigJson) as Record<string, unknown> : null;
  const summary = summaryJson ? parseJsonSafe(summaryJson) as Record<string, unknown> : null;
  const runScope = String(runConfig?.runScope ?? (templateName === "__domain__" && row.template_version === 0 ? "domain" : "template"));
  const templateCount = runConfig?.templateCount ?? null;
  return {
    id: row.id,
    benchRunId: dbText(row.bench_run_id),
    domainId: dbText(row.domain_id),
    templateName,
    templateVersion: row.template_version,
    runScope,
    templateCount,
    targetType: dbText(row.target_type),
    status: dbText(row.status),
    score: row.score,
    maxScore: row.max_score,
    passRate: row.pass_rate,
    model: dbNullableText(row.model),
    suite: dbNullableText(row.suite),
    scene: dbNullableText(row.scene),
    triggeredBy: dbNullableText(row.triggered_by),
    clawmindFlowId: dbNullableText(row.clawmind_flow_id) ?? pickClawmindFlowId(undefined, runConfig),
    sessionId: dbNullableText(row.session_id),
    sessionKey: dbNullableText(row.session_key),
    runConfig,
    summary,
    tokenUsage: extractRunTokenUsage(summary),
    errorText: dbNullableText(row.error_text),
    startedAt: row.started_at,
    completedAt: row.completed_at,
    ownerUserId: dbText(row.owner_user_id),
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}

function resultRowToApi(row: {
  id: number;
  result_id: unknown;
  bench_run_id: unknown;
  task_id: unknown;
  task_name: unknown;
  status: unknown;
  score: number | null;
  max_score: number | null;
  grading_type: unknown;
  execution_time_ms: number | null;
  transcript_path: unknown;
  workspace_path: unknown;
  result_json: unknown;
  breakdown_json: unknown;
  notes: unknown;
  error_text: unknown;
  gmt_create: number;
  gmt_modified: number;
}) {
  const resultJson = dbNullableText(row.result_json);
  const breakdownJson = dbNullableText(row.breakdown_json);
  const parsedResultJson = resultJson ? parseJsonSafe(resultJson) as Record<string, unknown> : null;
  const parsedBreakdown = breakdownJson ? parseJsonSafe(breakdownJson) as Record<string, unknown> : null;
  return {
    id: row.id,
    resultId: dbText(row.result_id),
    benchRunId: dbText(row.bench_run_id),
    taskId: dbText(row.task_id),
    taskName: dbNullableText(row.task_name),
    status: dbText(row.status),
    score: row.score,
    maxScore: row.max_score,
    gradingType: dbNullableText(row.grading_type),
    executionTimeMs: row.execution_time_ms,
    transcriptPath: dbNullableText(row.transcript_path),
    workspacePath: dbNullableText(row.workspace_path),
    resultJson: parsedResultJson,
    breakdown: parsedBreakdown,
    tokenUsage: extractTaskTokenUsage(parsedResultJson, parsedBreakdown),
    notes: dbNullableText(row.notes),
    errorText: dbNullableText(row.error_text),
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}

type TokenUsageApi = {
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  totalTokens?: number;
  requestCount?: number;
  costUsd?: number;
  raw?: unknown;
};

function extractRunTokenUsage(summary: Record<string, unknown> | null): TokenUsageApi | null {
  if (!summary) return null;
  const direct = normalizeTokenUsage(summary.tokenUsage);
  if (direct) return direct;
  const efficiency = normalizeEfficiencyUsage(summary.efficiency);
  if (efficiency) return efficiency;
  const progress = summary.progress;
  if (progress && typeof progress === "object" && !Array.isArray(progress)) {
    const progressUsage = normalizeTokenUsage((progress as Record<string, unknown>).tokenUsageSoFar);
    if (progressUsage) return progressUsage;
  }
  const raw = summary.raw;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const rawRecord = raw as Record<string, unknown>;
    return normalizeEfficiencyUsage(rawRecord.efficiency) ?? normalizeTokenUsage(rawRecord.tokenUsage);
  }
  return null;
}

function extractTaskTokenUsage(resultJson: Record<string, unknown> | null, breakdown: Record<string, unknown> | null): TokenUsageApi | null {
  return normalizeTokenUsage(resultJson?.tokenUsage)
    ?? normalizeTokenUsage(resultJson?.usage)
    ?? normalizeTokenUsage(breakdown?.tokenUsage)
    ?? normalizeTokenUsage(breakdown?.usage);
}

function normalizeEfficiencyUsage(value: unknown): TokenUsageApi | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const usage: TokenUsageApi = {
    inputTokens: toFiniteNumber(record.total_input_tokens),
    outputTokens: toFiniteNumber(record.total_output_tokens),
    totalTokens: toFiniteNumber(record.total_tokens),
    requestCount: toFiniteNumber(record.total_requests),
    costUsd: toFiniteNumber(record.total_cost_usd),
    raw: value,
  };
  return hasTokenNumbers(usage) ? usage : null;
}

function normalizeTokenUsage(value: unknown): TokenUsageApi | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const total = typeof record.total === "object" && record.total !== null && !Array.isArray(record.total)
    ? record.total as Record<string, unknown>
    : null;
  const usage: TokenUsageApi = {
    inputTokens: firstFiniteNumber(record.inputTokens, record.input_tokens, record.input, total?.inputTokens, total?.input_tokens),
    outputTokens: firstFiniteNumber(record.outputTokens, record.output_tokens, record.output, total?.outputTokens, total?.output_tokens),
    cacheReadTokens: firstFiniteNumber(record.cacheReadTokens, record.cache_read_tokens, record.cacheRead, total?.cacheReadTokens, total?.cache_read_tokens),
    cacheWriteTokens: firstFiniteNumber(record.cacheWriteTokens, record.cache_write_tokens, record.cacheWrite, total?.cacheWriteTokens, total?.cache_write_tokens),
    totalTokens: firstFiniteNumber(record.totalTokens, record.total_tokens, total?.totalTokens, total?.total_tokens),
    requestCount: firstFiniteNumber(record.requestCount, record.request_count, total?.requestCount, total?.request_count),
    costUsd: firstFiniteNumber(record.costUsd, record.cost_usd, total?.costUsd, total?.cost_usd),
    raw: value,
  };
  if (usage.totalTokens === undefined) {
    const input = usage.inputTokens ?? 0;
    const output = usage.outputTokens ?? 0;
    const cacheRead = usage.cacheReadTokens ?? 0;
    const cacheWrite = usage.cacheWriteTokens ?? 0;
    const computed = input + output + cacheRead + cacheWrite;
    if (computed > 0) usage.totalTokens = computed;
  }
  return hasTokenNumbers(usage) ? usage : null;
}

function hasTokenNumbers(usage: TokenUsageApi): boolean {
  return [
    usage.inputTokens,
    usage.outputTokens,
    usage.cacheReadTokens,
    usage.cacheWriteTokens,
    usage.totalTokens,
    usage.requestCount,
  ].some((value) => typeof value === "number" && Number.isFinite(value));
}

function firstFiniteNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    const n = toFiniteNumber(value);
    if (n !== undefined) return n;
  }
  return undefined;
}

function toFiniteNumber(value: unknown): number | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function artifactRowToApi(row: BenchArtifactRow, includeContent: boolean) {
  const summaryJson = dbNullableText(row.summary_json);
  const contentJson = includeContent ? dbNullableText(row.content_json) : null;
  return {
    id: row.id,
    artifactId: dbText(row.artifact_id),
    benchRunId: dbText(row.bench_run_id),
    resultId: dbNullableText(row.result_id),
    taskId: dbNullableText(row.task_id),
    artifactType: dbText(row.artifact_type),
    filename: dbNullableText(row.filename),
    contentType: dbNullableText(row.content_type),
    sizeBytes: row.size_bytes,
    storageType: dbText(row.storage_type),
    storagePath: dbNullableText(row.storage_path),
    summary: summaryJson ? parseJsonSafe(summaryJson) : null,
    sha256: dbNullableText(row.sha256),
    createdBy: dbNullableText(row.created_by),
    ownerUserId: dbText(row.owner_user_id),
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
    ...(includeContent ? {
      contentText: dbNullableText(row.content_text),
      contentJson: contentJson ? parseJsonSafe(contentJson) : null,
    } : {}),
  };
}

function isSessionArtifact(artifactType: string): boolean {
  return ["session", "session_trace", "session_transcript", "session_summary", "trajectory_jsonl"].includes(artifactType);
}

function sessionArtifactToSummary(row: BenchArtifactRow) {
  const summary = row.summary_json ? parseJsonSafe(row.summary_json) as Record<string, unknown> : {};
  const contentSummary = dbNullableText(row.content_text) ? summarizeSessionJsonl(dbText(row.content_text)) : {};
  const summaryUsage = normalizeTokenUsage(summary.usage);
  const contentUsage = normalizeTokenUsage(contentSummary.tokenUsage);
  const usage = contentUsage?.totalTokens ? contentUsage : summaryUsage ?? contentUsage;
  return {
    artifactId: dbText(row.artifact_id),
    benchRunId: dbText(row.bench_run_id),
    taskId: dbNullableText(row.task_id),
    artifactType: dbText(row.artifact_type),
    filename: dbNullableText(row.filename),
    contentType: dbNullableText(row.content_type),
    sizeBytes: row.size_bytes,
    eventCount: toFiniteNumber(summary.eventCount) ?? toFiniteNumber(contentSummary.eventCount) ?? null,
    messageCount: toFiniteNumber(summary.messageCount) ?? toFiniteNumber(contentSummary.messageCount) ?? null,
    toolCallCount: toFiniteNumber(summary.toolCallCount) ?? toFiniteNumber(contentSummary.toolCallCount) ?? null,
    totalTokens: toFiniteNumber(summary.totalTokens) ?? usage?.totalTokens ?? null,
    firstTimestamp: typeof summary.firstTimestamp === "string" ? summary.firstTimestamp : typeof contentSummary.firstTimestamp === "string" ? contentSummary.firstTimestamp : null,
    lastTimestamp: typeof summary.lastTimestamp === "string" ? summary.lastTimestamp : typeof contentSummary.lastTimestamp === "string" ? contentSummary.lastTimestamp : null,
    summary,
    gmtCreate: row.gmt_create,
  };
}

function sessionArtifactToDetail(row: BenchArtifactRow) {
  const contentText = dbNullableText(row.content_text);
  const contentJson = dbNullableText(row.content_json);
  const contentType = dbText(row.content_type).toLowerCase();
  return {
    ...sessionArtifactToSummary(row),
    contentText,
    contentJson: contentJson ? parseJsonSafe(contentJson) : null,
    events: contentText && (contentType.includes("jsonl") || contentType.includes("ndjson"))
      ? parseJsonlPreview(contentText, 500)
      : [],
  };
}

function parseJsonlPreview(contentText: string, limit: number): unknown[] {
  const events: unknown[] = [];
  for (const line of contentText.split(/\r?\n/)) {
    if (!line.trim()) continue;
    if (events.length >= limit) break;
    try {
      events.push(JSON.parse(line));
    } catch {
      events.push({ type: "parse_error", raw: line.slice(0, 1000) });
    }
  }
  return events;
}

function summarizeArtifactContent(artifactType: string, contentText: string | null, contentJson: string | null): string | null {
  if (contentText && isSessionArtifact(artifactType)) {
    return JSON.stringify(summarizeSessionJsonl(contentText));
  }
  if (contentJson) {
    const parsed = parseJsonSafe(contentJson);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      const record = parsed as Record<string, unknown>;
      if (record.efficiency || record.tasks) {
        return JSON.stringify({
          taskCount: Array.isArray(record.tasks) ? record.tasks.length : null,
          tokenUsage: normalizeEfficiencyUsage(record.efficiency),
        });
      }
    }
  }
  return null;
}

function summarizeSessionJsonl(contentText: string): Record<string, unknown> {
  const counts: Record<string, number> = {};
  const usage = { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheWriteTokens: 0, totalTokens: 0 };
  let eventCount = 0;
  let messageCount = 0;
  let toolCallCount = 0;
  let firstTimestamp: string | null = null;
  let lastTimestamp: string | null = null;

  for (const line of contentText.split(/\r?\n/)) {
    if (!line.trim()) continue;
    eventCount += 1;
    try {
      const event = JSON.parse(line) as Record<string, unknown>;
      const type = typeof event.type === "string" ? event.type : "unknown";
      counts[type] = (counts[type] ?? 0) + 1;
      if (type === "message") messageCount += 1;
      const ts = typeof event.timestamp === "string" ? event.timestamp : null;
      if (ts && !firstTimestamp) firstTimestamp = ts;
      if (ts) lastTimestamp = ts;
      const message = event.message && typeof event.message === "object" ? event.message as Record<string, unknown> : null;
      const content = Array.isArray(message?.content) ? message.content : [];
      for (const item of content) {
        if (item && typeof item === "object" && (item as Record<string, unknown>).type === "toolCall") {
          toolCallCount += 1;
        }
      }
      const eventUsage = normalizeTokenUsage(message?.usage);
      if (eventUsage) {
        usage.inputTokens += eventUsage.inputTokens ?? 0;
        usage.outputTokens += eventUsage.outputTokens ?? 0;
        usage.cacheReadTokens += eventUsage.cacheReadTokens ?? 0;
        usage.cacheWriteTokens += eventUsage.cacheWriteTokens ?? 0;
        usage.totalTokens += eventUsage.totalTokens ?? 0;
      }
    } catch {
      counts.parse_error = (counts.parse_error ?? 0) + 1;
    }
  }

  return {
    eventCount,
    messageCount,
    toolCallCount,
    totalTokens: usage.totalTokens || null,
    tokenUsage: usage.totalTokens > 0 ? usage : null,
    eventTypeCounts: counts,
    firstTimestamp,
    lastTimestamp,
  };
}

function inferArtifactContentType(artifactType: string, filename: unknown): string {
  const name = typeof filename === "string" ? filename.toLowerCase() : "";
  if (name.endsWith(".jsonl") || artifactType === "session_trace" || artifactType === "trajectory_jsonl") return "application/jsonl";
  if (name.endsWith(".json") || artifactType.endsWith("_raw") || artifactType === "benchmark_report") return "application/json";
  if (name.endsWith(".md")) return "text/markdown";
  return "text/plain";
}

function runIncludesTemplate(row: {
  template_name: unknown;
  run_config_json: unknown;
}, templateName: string): boolean {
  if (dbText(row.template_name) === templateName) return true;

  const runConfigJson = dbNullableText(row.run_config_json);
  const runConfig = runConfigJson ? parseJsonSafe(runConfigJson) as Record<string, unknown> : null;
  const templates = Array.isArray(runConfig?.templates) ? runConfig.templates : [];
  return templates.some((item) => {
    if (!item || typeof item !== "object") return false;
    return (item as Record<string, unknown>).templateName === templateName;
  });
}

function pickClawmindFlowId(value: unknown, runConfig?: Record<string, unknown> | null): string | null {
  const candidates = [
    value,
    runConfig?.clawmindFlowId,
    runConfig?.flowId,
    runConfig?.workflowFlowId,
    runConfig?.workflowRunId,
  ];
  for (const candidate of candidates) {
    if (candidate === null || candidate === undefined) continue;
    const text = String(candidate).trim();
    if (!text || (text.startsWith("{{") && text.endsWith("}}"))) continue;
    return text;
  }
  return null;
}

function dbNullableText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  return dbText(value);
}

function dbText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Buffer.isBuffer(value)) return value.toString("utf8");
  if (typeof value === "object") {
    const maybeBuffer = value as { type?: unknown; data?: unknown };
    if (maybeBuffer.type === "Buffer" && Array.isArray(maybeBuffer.data)) {
      return Buffer.from(maybeBuffer.data as number[]).toString("utf8");
    }
  }
  return String(value);
}

function parseJsonSafe(json: string): unknown {
  try { return JSON.parse(json); } catch { return json; }
}
