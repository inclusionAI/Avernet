import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { recordSkillRegistration, recordSkillTaskEvent, skillAuditTransaction, type SkillEventRow } from './skill-audit.js';

export type SkillAssetRow = {
  id: number;
  asset_id: string;
  owner_user_id: string;
  space_id: string | null;
  space_type: "PERSONAL" | "TEAM" | null;
  space_name: string | null;
  bot_id: string;
  ocb_skill_id: string;
  display_name: string;
  description: string | null;
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

  async listEvents(ownerUserId: string, teamSpaceIds: readonly string[] = [], assetId?: string) {
    return this.db.query<SkillEventRow>(
      `SELECT e.* FROM ce_skill_events e
       JOIN ce_skill_assets a ON a.asset_id = e.asset_id
       WHERE (e.owner_user_id = ?${teamSpaceIds.length
    ? ` OR (a.space_type = 'TEAM' AND a.space_id IN (${teamSpaceIds.map(() => '?').join(',')}))` : ''})
       ${assetId ? 'AND e.asset_id = ?' : ''}
       ORDER BY e.started_at DESC, e.id DESC`,
      [ownerUserId, ...teamSpaceIds, ...(assetId ? [assetId] : [])],
    );
  }

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

  async listAssets(ownerUserId: string, teamSpaceIds: readonly string[] = []): Promise<SkillAssetRow[]> {
    return this.db.query<SkillAssetRow>(
      `SELECT * FROM ce_skill_assets WHERE owner_user_id = ?${teamSpaceIds.length ? ` OR (space_type = 'TEAM' AND space_id IN (${teamSpaceIds.map(() => "?").join(",")}))` : ""} ORDER BY gmt_modified DESC, id DESC`,
      [ownerUserId, ...teamSpaceIds],
    );
  }

  async createAsset(input: {
    assetId: string;
    versionId: string;
    ownerUserId: string;
    spaceId?: string | null;
    spaceType?: "PERSONAL" | "TEAM" | null;
    spaceName?: string | null;
    actorId?: string;
    botId: string;
    ocbSkillId: string;
    displayName: string;
    description?: string | null;
    packageRef: string;
    packageSha256: string;
  }): Promise<SkillAssetRow> {
    await this.db.transaction(async (tx) => {
      const now = tx.dialect.now();
      await tx.exec(
        `INSERT INTO ce_skill_assets
         (asset_id, owner_user_id, space_id, space_type, space_name, bot_id, ocb_skill_id, display_name, description, current_version_no,
          current_package_ref, current_package_sha256, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)`,
        [input.assetId, input.ownerUserId, input.spaceId ?? null, input.spaceType ?? null, input.spaceName ?? null,
          input.botId, input.ocbSkillId, input.displayName, input.description ?? null,
          input.packageRef, input.packageSha256, now, now],
      );
      await tx.exec(
        `INSERT INTO ce_skill_versions
         (version_id, asset_id, version_no, package_ref, package_sha256, status, gmt_create)
         VALUES (?, ?, 1, ?, ?, 'baseline', ?)`,
        [input.versionId, input.assetId, input.packageRef, input.packageSha256, now],
      );
      await recordSkillRegistration(tx, { assetId: input.assetId, versionId: input.versionId,
        versionNo: 1, actorId: input.actorId ?? input.ownerUserId });
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
    appliedBy?: string;
  }): Promise<SkillVersionRow> {
    return skillAuditTransaction(this.db, async (tx) => {
      // Serialize version allocation and idempotency checks across workers.
      await tx.exec('UPDATE ce_skill_assets SET asset_id = asset_id WHERE asset_id = ?', [input.assetId]);
      const asset = (await tx.query<SkillAssetRow>('SELECT * FROM ce_skill_assets WHERE asset_id = ?', [input.assetId]))[0];
      if (!asset) throw new Error("Skill 不存在");
      const existing = (await tx.query<SkillVersionRow>('SELECT * FROM ce_skill_versions WHERE asset_id = ? AND source_task_id = ?',
        [input.assetId, input.sourceTaskId]))[0];
      if (existing) return existing;
      const versionNo = Number(asset.current_version_no) + 1;
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
      if (input.appliedBy) await recordSkillTaskEvent(tx, input.sourceTaskId, {
        status: 'waiting_acceptance', outcome: 'applied', actorType: 'user', actorId: input.appliedBy,
        versionToId: input.versionId, versionToNo: versionNo,
      });
      return (await tx.query<SkillVersionRow>('SELECT * FROM ce_skill_versions WHERE version_id = ?', [input.versionId]))[0];
    });
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

  async findVersionByNumber(assetId: string, versionNo: number): Promise<SkillVersionRow | null> {
    return (await this.db.query<SkillVersionRow>(
      "SELECT * FROM ce_skill_versions WHERE asset_id = ? AND version_no = ?",
      [assetId, versionNo],
    ))[0] ?? null;
  }

  async findVersionBySourceTask(assetId: string, sourceTaskId: string): Promise<SkillVersionRow | null> {
    return (await this.db.query<SkillVersionRow>(
      "SELECT * FROM ce_skill_versions WHERE asset_id = ? AND source_task_id = ?",
      [assetId, sourceTaskId],
    ))[0] ?? null;
  }
}
