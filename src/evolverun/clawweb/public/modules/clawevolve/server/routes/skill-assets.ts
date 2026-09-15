import { randomUUID } from "node:crypto";
import { Router, type Request, type ErrorRequestHandler } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { OcbLocalSkillPort, OcbRequestIdentity, OcbSpacePort } from "../internal/module-api.js";
import { canReadSpaceRecord, registrationSpace, spaceColumns } from "../services/evolve/space-access.js";
import type { SkillAssetRepository } from "../repositories/skill-asset-repository.js";
import { skillEventTestBench } from "../repositories/skill-audit.js";
import { getArtifactBucket, type ObjectStore } from "../services/object-storage/oss-object-store.js";
import { skillPackageDiff, skillPackageView } from "../services/evolve/skill-package-view.js";

type SkillAssetsRouterInput = {
  repo: SkillAssetRepository;
  ocbLocalSkills: OcbLocalSkillPort | null;
  ocbSpaces?: OcbSpacePort;
  artifactStore?: ObjectStore;
};

function identity(req: Request): OcbRequestIdentity | null {
  const userId = String(req.header("X-User-Id") ?? "").trim();
  if (!userId) return null;
  return {
    userId,
    authorization: req.header("Authorization") || undefined,
    cookie: req.header("Cookie") || undefined,
  };
}

type DisplayMetadata = { ownerId: string | null; descriptions: Map<string, string | null> };

function assetView(row: Awaited<ReturnType<SkillAssetRepository["findAsset"]>>, metadata?: DisplayMetadata) {
  if (!row) return null;
  return {
    assetId: row.asset_id,
    spaceId: row.space_id ?? null,
    spaceType: row.space_type ?? null,
    spaceName: row.space_name ?? null,
    ownerId: metadata?.ownerId ?? null,
    createdAt: row.gmt_create,
    botId: row.bot_id,
    skillId: row.ocb_skill_id,
    name: row.display_name,
    description: metadata?.descriptions.get(row.ocb_skill_id) ?? null,
    currentVersion: `v${row.current_version_no}`,
    updatedAt: row.gmt_modified,
  };
}

function objectKey(ref: string): string {
  const prefix = `oss://${getArtifactBucket()}/`;
  if (!ref.startsWith(prefix)) throw new Error("Skill 版本不属于当前文件存储");
  const value = ref.slice(prefix.length);
  if (!value || value.startsWith("/") || value.split("/").some((part) => !part || part === "." || part === "..")) {
    throw new Error("Skill 版本文件路径不合法");
  }
  return value;
}

function skillEventTaskConfig(value: string): {
  targetSkill?: { assetId?: string; baseline?: { sha256?: string; versionId?: string; versionNo?: number } };
} {
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

async function readSnapshot(store: ObjectStore, ref: string) {
  try {
    return await store.getObject(objectKey(ref));
  } catch (error) {
    const code = (error as { code?: unknown } | null)?.code;
    if (code !== "ENOENT" && code !== "NoSuchKey") throw error;
    throw Object.assign(new Error("该版本的历史快照在当前存储中不可用，无法查看内容或差异；不会以当前 Skill 内容替代。"), {
      code: "SKILL_SNAPSHOT_UNAVAILABLE",
    });
  }
}

export function createSkillAssetsRouter(input: SkillAssetsRouterInput): Router {
  const router = Router();

  async function readable(asset: NonNullable<Awaited<ReturnType<SkillAssetRepository["findAsset"]>>>, requestIdentity: OcbRequestIdentity) {
    return canReadSpaceRecord(asset, requestIdentity.userId,
      asset.space_id ? await input.ocbSpaces?.listAccessibleSpaces({ identity: requestIdentity }) ?? [] : []);
  }

  async function displayMetadata(botIds: string[], requestIdentity: OcbRequestIdentity, includeSkills = true) {
    // Request-scoped deduplication: two metadata calls per distinct Bot, never
    // per version/asset and never ZIP reads. Old registered assets benefit too.
    const entries = await Promise.all([...new Set(botIds)].map(async (botId) => {
      const query = { botId, identity: requestIdentity };
      const [bot, skills] = await Promise.all([
        input.ocbLocalSkills?.getBotMetadata?.(query).catch(() => null),
        includeSkills ? input.ocbLocalSkills?.listLocalSkills(query).catch(() => []) : [],
      ]);
      return [botId, { ownerId: bot?.ownerId ?? null,
        descriptions: new Map((skills ?? []).map((skill) => [skill.skillId, skill.description ?? null])),
      }] as const;
    }));
    return new Map(entries);
  }

  router.get("/skill-events", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    if (!requestIdentity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const [events, taskContexts, versions] = await Promise.all([
      input.repo.listEvents(requestIdentity.userId),
      input.repo.listEventTaskContexts(requestIdentity.userId),
      input.repo.listEventVersions(requestIdentity.userId),
    ]);
    const metadata = await displayMetadata(events.map((event) => event.bot_id), requestIdentity, false);
    const tasksById = new Map(taskContexts.map((task) => [task.task_id, task]));
    const versionsByDigest = new Map<string, typeof versions>();
    for (const version of versions) {
      const key = `${version.asset_id}\0${version.package_sha256}`;
      versionsByDigest.set(key, [...(versionsByDigest.get(key) ?? []), version]);
    }
    res.json({ items: events.map((event) => {
      const task = event.task_id ? tasksById.get(event.task_id) : null;
      const type = task?.task_type === "diagnose"
        ? event.event_type === "evolution_started" ? "diagnosis_started"
          : event.event_type === "evolution_finished" ? "diagnosis_finished" : event.event_type
        : event.event_type;
      let versionId = event.version_id;
      let versionNo = event.version_no;
      if (versionNo == null && task && ["diagnosis_started", "diagnosis_finished", "evolution_started", "evolution_finished"].includes(type)) {
        const target = skillEventTaskConfig(task.config_json).targetSkill;
        if (target?.assetId === event.asset_id) {
          if (typeof target.baseline?.versionId === "string" && Number.isSafeInteger(target.baseline.versionNo)) {
            versionId = target.baseline.versionId;
            versionNo = target.baseline.versionNo!;
          } else if (typeof target.baseline?.sha256 === "string") {
            const exact = versionsByDigest.get(`${event.asset_id}\0${target.baseline.sha256}`) ?? [];
            if (exact.length === 1) {
              versionId = exact[0].version_id;
              versionNo = exact[0].version_no;
            }
          }
        }
      }
      return {
      eventId: event.event_id, assetId: event.asset_id, name: event.display_name,
      description: event.description,
      ownerId: metadata.get(event.bot_id)?.ownerId ?? null, botId: event.bot_id,
      version: versionNo == null ? null : `v${versionNo}`, versionId,
      type, actorId: event.actor_id, actorType: event.actor_type, result: event.result,
      taskId: event.task_id, createdAt: event.gmt_create,
      testBench: skillEventTestBench(event.detail_json, event.task_id),
    }; }) });
  }));

  router.get("/skill-assets/available", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const botId = String(req.query.botId ?? "").trim();
    if (!requestIdentity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    if (!botId) { res.status(400).json({ error: "请选择 Bot" }); return; }
    if (!input.ocbLocalSkills) { res.status(503).json({ error: "OCB Skill 服务不可用" }); return; }
    const items = await input.ocbLocalSkills.listLocalSkills({ botId, identity: requestIdentity });
    res.json({ items });
  }));

  router.get("/skill-assets", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    if (!requestIdentity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const spaces = await input.ocbSpaces?.listAccessibleSpaces({ identity: requestIdentity }) ?? [];
    const assets = (await input.repo.listAssets(requestIdentity.userId, spaces.filter((space) => space.type === "TEAM").map((space) => space.id)))
      .filter((asset) => canReadSpaceRecord(asset, requestIdentity.userId, spaces));
    const metadata = await displayMetadata(assets.map((asset) => asset.bot_id), requestIdentity);
    res.json({ items: assets.map((asset) => assetView(asset, metadata.get(asset.bot_id))) });
  }));

  router.post("/skill-assets", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const botId = String(req.body?.botId ?? "").trim();
    const skillId = String(req.body?.skillId ?? "").trim();
    if (!requestIdentity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    if (!botId || !skillId) { res.status(400).json({ error: "请选择 Bot 中自己的 Skill" }); return; }
    if (!input.ocbLocalSkills || !input.artifactStore?.putObject) {
      res.status(503).json({ error: "Skill 登记所需服务不可用" }); return;
    }
    const space = await registrationSpace(input.ocbSpaces, requestIdentity, req.body?.spaceId);
    const existing = await input.repo.findByOcbSkill(requestIdentity.userId, botId, skillId);
    if (existing) {
      if (space && existing.space_id !== space.id) {
        res.status(409).json({ error: "该 Skill 已登记在其他空间，不能通过重复登记变更归属" }); return;
      }
      const metadata = await displayMetadata([botId], requestIdentity);
      res.json({ ...assetView(existing, metadata.get(botId)), existing: true }); return;
    }
    const exported = await input.ocbLocalSkills.exportLocalSkill({
      botId,
      skillId,
      identity: requestIdentity,
    });
    const assetId = `SKILL-${randomUUID().slice(0, 12).toUpperCase()}`;
    const objectKey = `evolve/skills/${assetId}/versions/v1/package.zip`;
    await input.artifactStore.putObject(objectKey, exported.packageBytes, "application/zip");
    const created = await input.repo.createAsset({
      assetId,
      versionId: `SKVER-${randomUUID().slice(0, 12).toUpperCase()}`,
      ownerUserId: requestIdentity.userId,
      ...spaceColumns(space),
      botId,
      ocbSkillId: skillId,
      displayName: exported.displayName,
      description: exported.description ?? null,
      packageRef: `oss://${getArtifactBucket()}/${objectKey}`,
      packageSha256: exported.sha256,
    });
    const metadata = await displayMetadata([botId], requestIdentity, false);
    metadata.get(botId)?.descriptions.set(skillId, exported.description ?? null);
    res.status(201).json(assetView(created, metadata.get(botId)));
  }));

  router.get("/skill-assets/:assetId", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || !await readable(asset, requestIdentity)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    const metadata = await displayMetadata([asset.bot_id], requestIdentity);
    res.json({
      ...assetView(asset, metadata.get(asset.bot_id)),
      versions: (await input.repo.listVersions(asset.asset_id)).map((version) => ({
        versionId: version.version_id,
        version: `v${version.version_no}`,
        status: version.status,
        sourceTaskId: version.source_task_id,
        packageSha256: version.package_sha256,
        createdAt: version.gmt_create,
      })),
    });
  }));

  router.get("/skill-assets/:assetId/versions/:versionId/content", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || !await readable(asset, requestIdentity)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    if (!input.artifactStore) { res.status(503).json({ error: "Skill 版本文件存储不可用" }); return; }
    const version = await input.repo.findVersion(asset.asset_id, String(req.params.versionId));
    if (!version) { res.status(404).json({ error: "Skill 版本不存在" }); return; }
    const stored = await readSnapshot(input.artifactStore, version.package_ref);
    res.json(await skillPackageView(stored.content, String(req.query.path ?? "")));
  }));

  router.get("/skill-assets/:assetId/versions/:versionId/diff", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || !await readable(asset, requestIdentity)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    if (!input.artifactStore) { res.status(503).json({ error: "Skill 版本文件存储不可用" }); return; }
    const version = await input.repo.findVersion(asset.asset_id, String(req.params.versionId));
    if (!version) { res.status(404).json({ error: "Skill 版本不存在" }); return; }
    if (!version.baseline_package_ref) {
      res.json({ baseline: null, files: [] }); return;
    }
    const [baseline, candidate] = await Promise.all([
      readSnapshot(input.artifactStore, version.baseline_package_ref),
      readSnapshot(input.artifactStore, version.package_ref),
    ]);
    res.json({
      baseline: { sha256: version.baseline_package_sha256 },
      ...await skillPackageDiff(baseline.content, candidate.content),
    });
  }));

  const snapshotError: ErrorRequestHandler = (error, _req, res, next) => {
    if (error?.code !== "SKILL_SNAPSHOT_UNAVAILABLE") { next(error); return; }
    res.status(404).json({ code: error.code, error: error.message });
  };
  router.use(snapshotError);
  const ocbSkillError: ErrorRequestHandler = (error, _req, res, next) => {
    const status = error?.status ?? error?.statusCode;
    const unavailable = error?.code === "OCB_LOCAL_SKILL_UNAVAILABLE" && (status === 502 || status === 503);
    const rejected = error?.code === "OCB_LOCAL_SKILL_REQUEST_FAILED"
      && Number.isInteger(status) && status >= 400 && status < 500;
    if ((!unavailable && !rejected) || typeof error?.message !== "string") { next(error); return; }
    res.status(status).json({ code: error.code, error: error.message });
  };
  router.use(ocbSkillError);
  return router;
}
