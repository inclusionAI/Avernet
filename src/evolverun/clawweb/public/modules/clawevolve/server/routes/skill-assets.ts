import { SkillPackageStorage } from "../services/object-storage/skill-package-storage.js";
import { applySkillVersion } from "../services/evolve/skill-application.js";
import { skillPackageContentDigest } from "../services/evolve/skill-package-view.js";
import { createHash, randomUUID } from "node:crypto";
import { Router, type Request, type ErrorRequestHandler } from "express";
import multer from "multer";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { BotSkillGateway } from "../contracts/bot-skill-gateway.js";
import type { RequestIdentity } from "../contracts/request-identity.js";
import type { SpaceDirectory } from "../contracts/space-directory.js";
import { canReadSpaceRecord, registrationSpace, spaceColumns } from "../services/evolve/space-access.js";
import type { SkillAssetRepository } from "../repositories/skill-asset-repository.js";
import { skillEventTestBench } from "../repositories/skill-audit.js";
import { type ObjectStore } from "../services/object-storage/oss-object-store.js";
import { editSkillPackage, parseSkillPackage, skillPackageDiff, skillPackagesEquivalent, skillPackageView } from "../services/evolve/skill-package-view.js";

const versionUpload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 16 * 1024 * 1024, files: 1 },
});

type SkillAssetsRouterInput = {
  repo: SkillAssetRepository;
  hostLocalSkills: BotSkillGateway | null;
  hostSpaces?: SpaceDirectory;
  artifactStore?: ObjectStore;
  skillPackages?: SkillPackageStorage;
};

function identity(req: Request): RequestIdentity | null {
  const userId = String(req.header("X-User-Id") ?? "").trim();
  if (!userId) return null;
  return {
    userId,
    authorization: req.header("Authorization") || undefined,
    cookie: req.header("Cookie") || undefined,
    referer: req.header("Referer") || undefined,
    origin: req.header("Origin") || undefined,
  };
}

type DisplayMetadata = { ownerId: string | null; descriptions: Map<string, string | null> };

function assetView(
  row: Awaited<ReturnType<SkillAssetRepository["findAsset"]>>,
  metadata?: DisplayMetadata,
  viewerUserId?: string,
) {
  if (!row) return null;
  return {
    assetId: row.asset_id,
    spaceId: row.space_id ?? null,
    spaceType: row.space_type ?? null,
    spaceName: row.space_name ?? null,
    ownerId: metadata?.ownerId ?? null,
    createdAt: row.gmt_create,
    botId: row.bot_id,
    skillId: row.external_skill_id,
    name: row.display_name,
    description: metadata?.descriptions.get(row.external_skill_id) ?? null,
    canEdit: viewerUserId === row.owner_user_id,
    currentVersion: `v${row.current_version_no}`,
    updatedAt: row.gmt_modified,
  };
}


async function readSnapshot(store: SkillPackageStorage, ref: string) {
  try {
    return await store.read(ref);
  } catch (error) {
    const code = (error as { code?: unknown } | null)?.code;
    if (code !== "ENOENT" && code !== "NoSuchKey") throw error;
    throw Object.assign(new Error("该版本的历史快照在当前存储中不可用，无法查看内容或差异；不会以当前 Skill 内容替代。"), {
      code: "SKILL_SNAPSHOT_UNAVAILABLE",
    });
  }
}

function eventView(
  event: Awaited<ReturnType<SkillAssetRepository["listEvents"]>>[number],
  ownerId: string | null,
) {
  const version = (id: string | null, no: number | null) => id && no != null
    ? { versionId: id, version: `v${no}` } : null;
  return {
    eventId: String(event.id),
    assetId: event.asset_id,
    name: event.display_name,
    description: event.description,
    ownerId,
    botId: event.bot_id,
    actorId: event.actor_id,
    actorType: event.actor_type,
    type: event.event_type,
    status: event.status,
    outcome: event.outcome,
    taskId: event.task_id,
    versionFrom: version(event.version_from_id, event.version_from_no),
    versionTo: version(event.version_to_id, event.version_to_no),
    waitingInteractionId: event.waiting_interaction_id,
    summary: event.summary,
    testBench: skillEventTestBench(event.detail_json, event.task_id),
    startedAt: event.started_at,
    completedAt: event.completed_at,
    updatedAt: event.gmt_modified,
  };
}

function versionView(version: Awaited<ReturnType<SkillAssetRepository["findVersion"]>>) {
  if (!version) return null;
  return {
    versionId: version.version_id,
    version: `v${version.version_no}`,
    status: version.status,
    sourceTaskId: version.source_task_id,
    packageSha256: version.package_sha256,
    creationKind: version.creation_kind,
    createdBy: version.created_by,
    sourceVersion: version.source_version_id && version.source_version_no != null
      ? { versionId: version.source_version_id, version: `v${version.source_version_no}` }
      : null,
    createdAt: version.gmt_create,
  };
}

export function createSkillAssetsRouter(input: SkillAssetsRouterInput): Router {
  const router = Router();
  const packages = input.skillPackages ?? new SkillPackageStorage(undefined, input.artifactStore);

  async function readable(asset: NonNullable<Awaited<ReturnType<SkillAssetRepository["findAsset"]>>>, requestIdentity: RequestIdentity) {
    return canReadSpaceRecord(asset, requestIdentity.userId,
      asset.space_id ? await input.hostSpaces?.listAccessibleSpaces({ identity: requestIdentity }) ?? [] : []);
  }

  async function displayMetadata(botIds: string[], requestIdentity: RequestIdentity, includeSkills = true) {
    // Request-scoped deduplication: two metadata calls per distinct Bot, never
    // per version/asset and never ZIP reads. Old registered assets benefit too.
    const entries = await Promise.all([...new Set(botIds)].map(async (botId) => {
      const query = { botId, identity: requestIdentity };
      const [bot, skills] = await Promise.all([
        input.hostLocalSkills?.getBotMetadata?.(query).catch(() => null),
        includeSkills ? input.hostLocalSkills?.listLocalSkills(query).catch(() => []) : [],
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
    const spaces = await input.hostSpaces?.listAccessibleSpaces({ identity: requestIdentity }) ?? [];
    const events = await input.repo.listEvents(requestIdentity.userId,
      spaces.filter((space) => space.type === 'TEAM').map((space) => space.id));
    const metadata = await displayMetadata(events.map((event) => event.bot_id), requestIdentity, false);
    res.json({ items: events.map((event) => eventView(event, metadata.get(event.bot_id)?.ownerId ?? null)) });
  }));

  router.get("/skill-assets/available", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const botId = String(req.query.botId ?? "").trim();
    if (!requestIdentity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    if (!botId) { res.status(400).json({ error: "请选择 Bot" }); return; }
    if (!input.hostLocalSkills) { res.status(503).json({ error: "宿主 Skill 服务不可用" }); return; }
    const items = await input.hostLocalSkills.listLocalSkills({ botId, identity: requestIdentity });
    res.json({ items });
  }));

  router.get("/skill-assets", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    if (!requestIdentity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const spaces = await input.hostSpaces?.listAccessibleSpaces({ identity: requestIdentity }) ?? [];
    const assets = (await input.repo.listAssets(requestIdentity.userId, spaces.filter((space) => space.type === "TEAM").map((space) => space.id)))
      .filter((asset) => canReadSpaceRecord(asset, requestIdentity.userId, spaces));
    const metadata = await displayMetadata(assets.map((asset) => asset.bot_id), requestIdentity);
    res.json({ items: assets.map((asset) => assetView(asset, metadata.get(asset.bot_id), requestIdentity.userId)) });
  }));

  router.post("/skill-assets", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const botId = String(req.body?.botId ?? "").trim();
    const skillId = String(req.body?.skillId ?? "").trim();
    if (!requestIdentity) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    if (!botId || !skillId) { res.status(400).json({ error: "请选择 Bot 中自己的 Skill" }); return; }
    if (!input.hostLocalSkills || !packages.canWrite) {
      res.status(503).json({ error: "Skill 登记所需服务不可用" }); return;
    }
    const space = await registrationSpace(input.hostSpaces, requestIdentity, req.body?.spaceId);
    const existing = await input.repo.findByExternalSkill(requestIdentity.userId, botId, skillId);
    if (existing) {
      if (space && existing.space_id !== space.id) {
        res.status(409).json({ error: "该 Skill 已登记在其他空间，不能通过重复登记变更归属" }); return;
      }
      const metadata = await displayMetadata([botId], requestIdentity);
      res.json({ ...assetView(existing, metadata.get(botId), requestIdentity.userId), existing: true }); return;
    }
    const exported = await input.hostLocalSkills.exportLocalSkill({
      botId,
      skillId,
      ownerUserId: requestIdentity.userId,
      identity: requestIdentity,
    });
    const assetId = `SKILL-${randomUUID().slice(0, 12).toUpperCase()}`;
    const objectKey = `skills/${assetId}/versions/v1/package.zip`;
    await packages.put(objectKey, exported.packageBytes);
    const created = await input.repo.createAsset({
      assetId,
      versionId: `SKVER-${randomUUID().slice(0, 12).toUpperCase()}`,
      ownerUserId: requestIdentity.userId,
      ...spaceColumns(space),
      botId,
      externalSkillId: skillId,
      displayName: exported.displayName,
      description: exported.description ?? null,
      packageRef: packages.ref(objectKey),
      packageSha256: exported.sha256,
    });
    const metadata = await displayMetadata([botId], requestIdentity, false);
    metadata.get(botId)?.descriptions.set(skillId, exported.description ?? null);
    res.status(201).json(assetView(created, metadata.get(botId), requestIdentity.userId));
  }));

  router.post("/skill-assets/:assetId/versions", versionUpload.single("package"), asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || asset.owner_user_id !== requestIdentity.userId
      || !await readable(asset, requestIdentity)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    if (!input.hostLocalSkills || !packages.canWrite) {
      res.status(503).json({ error: "Skill 版本升级所需服务不可用" }); return;
    }
    const mode = String(req.body?.mode ?? "");
    const baseVersionId = String(req.body?.baseVersionId ?? "").trim();
    if (!['edit', 'upload', 'rollback'].includes(mode) || !baseVersionId) {
      res.status(400).json({ error: "版本编辑请求不完整" }); return;
    }
    const creationKind = mode as 'edit' | 'upload' | 'rollback';
    const base = await input.repo.findVersion(asset.asset_id, baseVersionId);
    if (!base) {
      res.status(409).json({ code: "SKILL_VERSION_CONFLICT", error: "Skill 已产生新版本，请刷新后重试" }); return;
    }
    const baseline = await readSnapshot(packages, base.package_ref);
    let sourceVersion = base;
    let candidate: Buffer;
    try {
      if (mode === 'edit') {
        if (!Array.isArray(req.body?.edits)) throw new Error("请提供需要编辑的文件");
        candidate = await editSkillPackage(baseline.content, req.body.edits.map((item: unknown) => ({
          path: String((item as { path?: unknown })?.path ?? ""),
          content: String((item as { content?: unknown })?.content ?? ""),
        })));
      } else if (mode === 'upload') {
        if (!req.file?.buffer.length) throw new Error("请选择 Skill ZIP 包");
        candidate = Buffer.from(req.file.buffer);
        await parseSkillPackage(candidate);
      } else {
        const sourceVersionId = String(req.body?.sourceVersionId ?? '').trim();
        if (!sourceVersionId || sourceVersionId === baseVersionId) throw new Error("请选择要回滚的历史版本");
        const selected = await input.repo.findVersion(asset.asset_id, sourceVersionId);
        if (!selected) throw new Error("要回滚的历史版本不存在");
        sourceVersion = selected;
        candidate = (await readSnapshot(packages, selected.package_ref)).content;
      }
    } catch (error) {
      res.status(400).json({ error: error instanceof Error ? error.message : "Skill 编辑内容不合法" }); return;
    }
    if (await skillPackagesEquivalent(baseline.content, candidate)) {
      res.status(400).json({ error: "Skill 内容没有变化" }); return;
    }
    const operationId = createHash("sha256").update(JSON.stringify([
      asset.asset_id, requestIdentity.userId, baseVersionId, creationKind,
      sourceVersion.version_id, await skillPackageContentDigest(candidate),
    ])).digest("hex");
    const versionId = `SKVER-${operationId.slice(0, 32).toUpperCase()}`;
    const existingVersion = await input.repo.findVersion(asset.asset_id, versionId);
    if (existingVersion) { res.json(versionView(existingVersion)); return; }
    const packageDigest = createHash("sha256").update(candidate).digest("hex");
    const key = `skills/${asset.asset_id}/versions/${versionId}/${packageDigest}.zip`;
    // Freeze bytes once; repeated submissions may have different ZIP metadata.
    const pending = asset.pending_application_json ? JSON.parse(asset.pending_application_json) : null;
    if (pending?.operationId !== operationId) await packages.put(key, candidate);
    let created;
    try {
      const applied = await applySkillVersion({
        repo: input.repo, host: input.hostLocalSkills, operationId, baselineBytes: baseline.content,
        readPackage: async ref => (await readSnapshot(packages, ref)).content,
        replace: { botId: asset.bot_id, skillId: asset.external_skill_id, expectedSha256: base.package_sha256,
          ownerUserId: asset.owner_user_id, identity: requestIdentity },
        version: { kind: "manual", data: {
          versionId, assetId: asset.asset_id, baseVersionId,
          packageRef: packages.ref(key), packageSha256: `sha256:${packageDigest}`,
          creationKind, sourceVersionId: sourceVersion.version_id,
          createdBy: requestIdentity.userId,
        } },
      });
      created = applied.version;
    } catch (error) {
      if ((error as { status?: number; code?: string }).status === 409 || (error as { code?: string }).code === "SKILL_VERSION_CONFLICT") {
        res.status(409).json({ code: "SKILL_VERSION_CONFLICT", error: (error as Error).message }); return;
      }
      throw error;
    }
    res.status(201).json(versionView(created));
  }));

  router.get("/skill-assets/:assetId", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || !await readable(asset, requestIdentity)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    const metadata = await displayMetadata([asset.bot_id], requestIdentity);
    res.json({
      ...assetView(asset, metadata.get(asset.bot_id), requestIdentity.userId),
      versions: (await input.repo.listVersions(asset.asset_id)).map(versionView),
    });
  }));

  router.get("/skill-assets/:assetId/history", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || !await readable(asset, requestIdentity)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    const spaces = await input.hostSpaces?.listAccessibleSpaces({ identity: requestIdentity }) ?? [];
    const events = await input.repo.listEvents(requestIdentity.userId,
      spaces.filter((space) => space.type === 'TEAM').map((space) => space.id), asset.asset_id);
    const metadata = await displayMetadata([asset.bot_id], requestIdentity, false);
    res.json({ events: events.map((event) => eventView(event, metadata.get(event.bot_id)?.ownerId ?? null)) });
  }));

  router.get("/skill-assets/:assetId/versions/:versionId/content", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || !await readable(asset, requestIdentity)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    if (!input.skillPackages && !input.artifactStore) { res.status(503).json({ error: "Skill 版本文件存储不可用" }); return; }
    const version = await input.repo.findVersion(asset.asset_id, String(req.params.versionId));
    if (!version) { res.status(404).json({ error: "Skill 版本不存在" }); return; }
    const stored = await readSnapshot(packages, version.package_ref);
    res.json(await skillPackageView(stored.content, String(req.query.path ?? "")));
  }));

  router.get("/skill-assets/:assetId/versions/:versionId/diff", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || !await readable(asset, requestIdentity)) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    if (!input.skillPackages && !input.artifactStore) { res.status(503).json({ error: "Skill 版本文件存储不可用" }); return; }
    const version = await input.repo.findVersion(asset.asset_id, String(req.params.versionId));
    if (!version) { res.status(404).json({ error: "Skill 版本不存在" }); return; }
    if (!version.baseline_package_ref) {
      res.json({ baseline: null, files: [] }); return;
    }
    const [baseline, candidate] = await Promise.all([
      readSnapshot(packages, version.baseline_package_ref),
      readSnapshot(packages, version.package_ref),
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
  const hostSkillError: ErrorRequestHandler = (error, _req, res, next) => {
    const status = error?.status ?? error?.statusCode;
    const sizeRejected = (error?.code === "HOST_LOCAL_SKILL_TOO_LARGE" && status === 413)
      || (error?.code === "HOST_LOCAL_SKILL_SIZE_UNAVAILABLE" && status === 502);
    const unavailable = error?.code === "HOST_LOCAL_SKILL_UNAVAILABLE" && (status === 502 || status === 503);
    const rejected = error?.code === "HOST_LOCAL_SKILL_REQUEST_FAILED"
      && Number.isInteger(status) && status >= 400 && status < 500;
    if ((!unavailable && !rejected && !sizeRejected) || typeof error?.message !== "string") { next(error); return; }
    res.status(status).json({ code: error.code, error: error.message });
  };
  router.use(hostSkillError);
  return router;
}
