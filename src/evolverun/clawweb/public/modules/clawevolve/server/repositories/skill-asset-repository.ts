import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type SkillAssetRow = {
  id: number;
  asset_id: string;
  owner_user_id: string;
  bot_id: string;
  ocb_skill_id: string;
  display_name: string;
  current_version_no: number;
  current_package_ref: string;
  current_package_sha256: string;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type SkillVersionRow = {
  id: number;
  version_id: string;
  asset_id: string;
  version_no: number;
  package_ref: string;
  package_sha256: string;
  source_task_id: string | null;
  baseline_package_ref: string | null;
  baseline_package_sha256: string | null;
  status: "baseline" | "accepted" | "rejected";
  gmt_create: number | string;
};

export class SkillAssetRepository {
  constructor(private readonly db: IDatabase) {}

  async findAsset(assetId: string): Promise<SkillAssetRow | null> {
    return (await this.db.query<SkillAssetRow>(
      "SELECT * FROM ce_skill_assets WHERE asset_id = ?",
      [assetId],
    ))[0] ?? null;
  }

  async findByOcbSkill(ownerUserId: string, botId: string, ocbSkillId: string): Promise<SkillAssetRow | null> {
    return (await this.db.query<SkillAssetRow>(
      `SELECT * FROM ce_skill_assets
       WHERE owner_user_id = ? AND bot_id = ? AND ocb_skill_id = ?`,
      [ownerUserId, botId, ocbSkillId],
    ))[0] ?? null;
  }

  async listAssets(ownerUserId: string): Promise<SkillAssetRow[]> {
    return this.db.query<SkillAssetRow>(
      "SELECT * FROM ce_skill_assets WHERE owner_user_id = ? ORDER BY gmt_modified DESC, id DESC",
      [ownerUserId],
    );
  }

  async createAsset(input: {
    assetId: string;
    versionId: string;
    ownerUserId: string;
    botId: string;
    ocbSkillId: string;
    displayName: string;
    packageRef: string;
    packageSha256: string;
  }): Promise<SkillAssetRow> {
    await this.db.transaction(async (tx) => {
      const now = tx.dialect.now();
      await tx.exec(
        `INSERT INTO ce_skill_assets
         (asset_id, owner_user_id, bot_id, ocb_skill_id, display_name, current_version_no,
          current_package_ref, current_package_sha256, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)`,
        [input.assetId, input.ownerUserId, input.botId, input.ocbSkillId, input.displayName,
          input.packageRef, input.packageSha256, now, now],
      );
      await tx.exec(
        `INSERT INTO ce_skill_versions
         (version_id, asset_id, version_no, package_ref, package_sha256, status, gmt_create)
         VALUES (?, ?, 1, ?, ?, 'baseline', ?)`,
        [input.versionId, input.assetId, input.packageRef, input.packageSha256, now],
      );
    });
    const created = await this.findAsset(input.assetId);
    if (!created) throw new Error("Skill 登记失败");
    return created;
  }

  async createAcceptedVersion(input: {
    versionId: string;
    assetId: string;
    packageRef: string;
    packageSha256: string;
    sourceTaskId: string;
    baselinePackageRef: string;
    baselinePackageSha256: string;
  }): Promise<SkillVersionRow> {
    const existing = await this.findVersionBySourceTask(input.assetId, input.sourceTaskId);
    if (existing) return existing;
    const asset = await this.findAsset(input.assetId);
    if (!asset) throw new Error("Skill 不存在");
    const versionNo = Number(asset.current_version_no) + 1;
    await this.db.transaction(async (tx) => {
      const now = tx.dialect.now();
      await tx.exec(
        `INSERT INTO ce_skill_versions
         (version_id, asset_id, version_no, package_ref, package_sha256, source_task_id,
          baseline_package_ref, baseline_package_sha256, status, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)`,
        [input.versionId, input.assetId, versionNo, input.packageRef, input.packageSha256,
          input.sourceTaskId, input.baselinePackageRef, input.baselinePackageSha256, now],
      );
      await tx.exec(
        `UPDATE ce_skill_assets SET current_version_no = ?, current_package_ref = ?,
         current_package_sha256 = ?, gmt_modified = ? WHERE asset_id = ?`,
        [versionNo, input.packageRef, input.packageSha256, now, input.assetId],
      );
    });
    return (await this.db.query<SkillVersionRow>(
      "SELECT * FROM ce_skill_versions WHERE version_id = ?",
      [input.versionId],
    ))[0];
  }

  async listVersions(assetId: string): Promise<SkillVersionRow[]> {
    return this.db.query<SkillVersionRow>(
      "SELECT * FROM ce_skill_versions WHERE asset_id = ? ORDER BY version_no DESC",
      [assetId],
    );
  }

  async findVersion(assetId: string, versionId: string): Promise<SkillVersionRow | null> {
    return (await this.db.query<SkillVersionRow>(
      "SELECT * FROM ce_skill_versions WHERE asset_id = ? AND version_id = ?",
      [assetId, versionId],
    ))[0] ?? null;
  }

  async findVersionBySourceTask(assetId: string, sourceTaskId: string): Promise<SkillVersionRow | null> {
    return (await this.db.query<SkillVersionRow>(
      "SELECT * FROM ce_skill_versions WHERE asset_id = ? AND source_task_id = ?",
      [assetId, sourceTaskId],
    ))[0] ?? null;
  }
}
