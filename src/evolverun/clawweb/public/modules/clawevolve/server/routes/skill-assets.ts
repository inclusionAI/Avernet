import { randomUUID } from "node:crypto";
import { Router, type Request } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { OcbLocalSkillPort, OcbRequestIdentity } from "../internal/module-api.js";
import type { SkillAssetRepository } from "../repositories/skill-asset-repository.js";
import { getArtifactBucket, type ObjectStore } from "../services/object-storage/oss-object-store.js";
import { skillPackageDiff, skillPackageView } from "../services/evolve/skill-package-view.js";

type SkillAssetsRouterInput = {
  repo: SkillAssetRepository;
  ocbLocalSkills: OcbLocalSkillPort | null;
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

function assetView(row: Awaited<ReturnType<SkillAssetRepository["findAsset"]>>) {
  if (!row) return null;
  return {
    assetId: row.asset_id,
    botId: row.bot_id,
    skillId: row.ocb_skill_id,
    name: row.display_name,
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

export function createSkillAssetsRouter(input: SkillAssetsRouterInput): Router {
  const router = Router();

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
    res.json({ items: (await input.repo.listAssets(requestIdentity.userId)).map(assetView) });
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
    const existing = await input.repo.findByOcbSkill(requestIdentity.userId, botId, skillId);
    if (existing) { res.json({ ...assetView(existing), existing: true }); return; }
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
      botId,
      ocbSkillId: skillId,
      displayName: exported.displayName,
      packageRef: `oss://${getArtifactBucket()}/${objectKey}`,
      packageSha256: exported.sha256,
    });
    res.status(201).json(assetView(created));
  }));

  router.get("/skill-assets/:assetId", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || asset.owner_user_id !== requestIdentity.userId) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    res.json({
      ...assetView(asset),
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
    if (!requestIdentity || !asset || asset.owner_user_id !== requestIdentity.userId) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    if (!input.artifactStore) { res.status(503).json({ error: "Skill 版本文件存储不可用" }); return; }
    const version = await input.repo.findVersion(asset.asset_id, String(req.params.versionId));
    if (!version) { res.status(404).json({ error: "Skill 版本不存在" }); return; }
    const stored = await input.artifactStore.getObject(objectKey(version.package_ref));
    res.json(await skillPackageView(stored.content, String(req.query.path ?? "")));
  }));

  router.get("/skill-assets/:assetId/versions/:versionId/diff", asyncHandler(async (req, res) => {
    const requestIdentity = identity(req);
    const asset = await input.repo.findAsset(String(req.params.assetId));
    if (!requestIdentity || !asset || asset.owner_user_id !== requestIdentity.userId) {
      res.status(404).json({ error: "Skill 不存在" }); return;
    }
    if (!input.artifactStore) { res.status(503).json({ error: "Skill 版本文件存储不可用" }); return; }
    const version = await input.repo.findVersion(asset.asset_id, String(req.params.versionId));
    if (!version) { res.status(404).json({ error: "Skill 版本不存在" }); return; }
    if (!version.baseline_package_ref) {
      res.json({ baseline: null, files: [] }); return;
    }
    const [baseline, candidate] = await Promise.all([
      input.artifactStore.getObject(objectKey(version.baseline_package_ref)),
      input.artifactStore.getObject(objectKey(version.package_ref)),
    ]);
    res.json({
      baseline: { sha256: version.baseline_package_sha256 },
      ...await skillPackageDiff(baseline.content, candidate.content),
    });
  }));

  return router;
}
