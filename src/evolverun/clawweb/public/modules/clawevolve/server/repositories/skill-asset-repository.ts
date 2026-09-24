import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { randomUUID } from "node:crypto";
import { recordSkillRegistration, recordSkillTaskEvent, skillAuditTransaction, type SkillEventRow } from './skill-audit.js';

export type SkillAssetRow = {
  id: number;
  asset_id: string;
  owner_user_id: string;
  space_id: string | null;
  space_type: "PERSONAL" | "TEAM" | null;
  space_name: string | null;
  bot_id: string;
  external_skill_id: string;
  pending_application_json: string | null;
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
  creation_kind: "registered" | "task" | "edit" | "upload" | "rollback";
  source_version_id: string | null;
  source_version_no: number | null;
  created_by: string | null;
  status: "baseline" | "accepted" | "rejected";
  gmt_create: number | string;
};

const skillAssetProjection = `SELECT a.*, v.package_ref AS current_package_ref,
  v.package_sha256 AS current_package_sha256 FROM ce_skill_assets a
  JOIN ce_skill_versions v ON v.asset_id = a.asset_id AND v.version_no = a.current_version_no`;
const versionProjection = `SELECT v.*, source.version_no AS source_version_no
  FROM ce_skill_versions v LEFT JOIN ce_skill_versions source ON source.version_id = v.source_version_id`;

export class SkillAssetRepository {
  constructor(private readonly db: IDatabase) {}

  /** Only release after the host proves that no write was started. */
  async releaseUnstartedApplication(assetId: string, operationId: string, reservationId: string) {
    await skillAuditTransaction(this.db, async (tx) => {
      await tx.exec('UPDATE ce_skill_assets SET asset_id = asset_id WHERE asset_id = ?', [assetId]);
      const asset = (await tx.query<SkillAssetRow>(`${skillAssetProjection} WHERE a.asset_id = ?`, [assetId]))[0];
      const pending = asset?.pending_application_json ? JSON.parse(asset.pending_application_json) : null;
      // A second caller may already be writing the same operation. Only the
      // original, unshared reservation may be released before its first write.
      if (pending?.operationId === operationId && pending.reservationId === reservationId) {
        await tx.exec('UPDATE ce_skill_assets SET pending_application_json = NULL WHERE asset_id = ?', [assetId]);
      }
    });
  }

  /** Serialize application intent, without holding a transaction across host I/O. */
  async reserveApplication(assetId: string, intent: { operationId: string; packageRef: string; packageSha256: string }, baseVersionId?: string,
    completed?: { versionId: string; sourceTaskId?: string }) {
    return skillAuditTransaction(this.db, async (tx) => {
      await tx.exec('UPDATE ce_skill_assets SET asset_id = asset_id WHERE asset_id = ?', [assetId]);
      const asset = (await tx.query<SkillAssetRow>(`${skillAssetProjection} WHERE a.asset_id = ?`, [assetId]))[0];
      if (!asset) throw new Error("Skill 不存在");
      if (completed) {
        const existing = (await tx.query<SkillVersionRow>(completed.sourceTaskId
          ? `${versionProjection} WHERE v.asset_id = ? AND v.source_task_id = ?`
          : `${versionProjection} WHERE v.asset_id = ? AND v.version_id = ?`,
        [assetId, completed.sourceTaskId ?? completed.versionId]))[0];
        if (existing) {
          const pending = asset.pending_application_json ? JSON.parse(asset.pending_application_json) : null;
          if (pending?.operationId === intent.operationId) {
            await tx.exec('UPDATE ce_skill_assets SET pending_application_json = NULL WHERE asset_id = ?', [assetId]);
          }
          return { completedVersion: existing, intent: null, resumed: true };
        }
      }
      const reservationId = randomUUID();
      if (asset.pending_application_json) {
        const pending = JSON.parse(asset.pending_application_json) as typeof intent;
        if (pending.operationId !== intent.operationId) {
          throw Object.assign(new Error("该 Skill 有尚未完成的版本应用，请先重试原操作"), { status: 409, code: "SKILL_VERSION_CONFLICT" });
        }
        const resumed = { ...pending, reservationId };
        await tx.exec('UPDATE ce_skill_assets SET pending_application_json = ? WHERE asset_id = ?', [JSON.stringify(resumed), assetId]);
        return { completedVersion: null, intent: resumed, resumed: true };
      }
      if (baseVersionId) {
        const base = (await tx.query<SkillVersionRow>(`${versionProjection} WHERE v.version_id = ? AND v.asset_id = ?`, [baseVersionId, assetId]))[0];
        if (!base || Number(base.version_no) !== Number(asset.current_version_no)) {
          throw Object.assign(new Error("Skill 已产生新版本，请刷新后重试"), { status: 409, code: "SKILL_VERSION_CONFLICT" });
        }
      }
      const created = { ...intent, reservationId };
      await tx.exec('UPDATE ce_skill_assets SET pending_application_json = ? WHERE asset_id = ?', [JSON.stringify(created), assetId]);
      return { completedVersion: null, intent: created, resumed: false };
    });
  }

  async listEvents(ownerUserId: string, teamSpaceIds: readonly string[] = [], assetId?: string) {
    return this.db.query<SkillEventRow>(
      `SELECT e.*, vf.version_no AS version_from_no, vt.version_no AS version_to_no FROM ce_skill_events e
       LEFT JOIN ce_skill_versions vf ON vf.version_id = e.version_from_id
       LEFT JOIN ce_skill_versions vt ON vt.version_id = e.version_to_id
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
      `${skillAssetProjection} WHERE a.asset_id = ?`,
      [assetId],
    ))[0] ?? null;
  }

  async findByExternalSkill(ownerUserId: string, botId: string, externalSkillId: string): Promise<SkillAssetRow | null> {
    return (await this.db.query<SkillAssetRow>(
      `${skillAssetProjection}
       WHERE a.owner_user_id = ? AND a.bot_id = ? AND a.external_skill_id = ?`,
      [ownerUserId, botId, externalSkillId],
    ))[0] ?? null;
  }

  async listAssets(ownerUserId: string, teamSpaceIds: readonly string[] = []): Promise<SkillAssetRow[]> {
    return this.db.query<SkillAssetRow>(
      `${skillAssetProjection} WHERE a.owner_user_id = ?${teamSpaceIds.length ? ` OR (a.space_type = 'TEAM' AND a.space_id IN (${teamSpaceIds.map(() => "?").join(",")}))` : ""} ORDER BY a.gmt_modified DESC, a.id DESC`,
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
    externalSkillId: string;
    displayName: string;
    description?: string | null;
    packageRef: string;
    packageSha256: string;
  }): Promise<SkillAssetRow> {
    await this.db.transaction(async (tx) => {
      const now = tx.dialect.now();
      await tx.exec(
        `INSERT INTO ce_skill_assets
         (asset_id, owner_user_id, space_id, space_type, space_name, bot_id, external_skill_id, display_name, description, current_version_no, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)`,
        [input.assetId, input.ownerUserId, input.spaceId ?? null, input.spaceType ?? null, input.spaceName ?? null,
          input.botId, input.externalSkillId, input.displayName, input.description ?? null,
          now, now],
      );
      await tx.exec(
        `INSERT INTO ce_skill_versions
         (version_id, asset_id, version_no, package_ref, package_sha256, creation_kind, created_by, status, gmt_create)
         VALUES (?, ?, 1, ?, ?, 'registered', ?, 'baseline', ?)`,
        [input.versionId, input.assetId, input.packageRef, input.packageSha256, input.actorId ?? input.ownerUserId, now],
      );
      await recordSkillRegistration(tx, { assetId: input.assetId, versionId: input.versionId,
        actorId: input.actorId ?? input.ownerUserId });
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
    operationId?: string;
  }): Promise<SkillVersionRow> {
    return skillAuditTransaction(this.db, async (tx) => {
      // Serialize version allocation and idempotency checks across workers.
      await tx.exec('UPDATE ce_skill_assets SET asset_id = asset_id WHERE asset_id = ?', [input.assetId]);
      const asset = (await tx.query<SkillAssetRow>(`${skillAssetProjection} WHERE a.asset_id = ?`, [input.assetId]))[0];
      if (!asset) throw new Error("Skill 不存在");
      const existing = (await tx.query<SkillVersionRow>(`${versionProjection} WHERE v.asset_id = ? AND v.source_task_id = ?`,
        [input.assetId, input.sourceTaskId]))[0];
      if (existing) return existing;
      const versionNo = Number(asset.current_version_no) + 1;
      const now = tx.dialect.now();
      await tx.exec(
        `INSERT INTO ce_skill_versions
         (version_id, asset_id, version_no, package_ref, package_sha256, source_task_id,
          baseline_package_ref, baseline_package_sha256, created_by, status, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)`,
        [input.versionId, input.assetId, versionNo, input.packageRef, input.packageSha256,
          input.sourceTaskId, input.baselinePackageRef, input.baselinePackageSha256, input.appliedBy ?? null, now],
      );
      await tx.exec(
        `UPDATE ce_skill_assets SET current_version_no = ?, gmt_modified = ? WHERE asset_id = ?`,
        [versionNo, now, input.assetId],
      );
      if (input.appliedBy) await recordSkillTaskEvent(tx, input.sourceTaskId, {
        status: 'waiting_acceptance', outcome: 'applied', actorType: 'user', actorId: input.appliedBy,
        versionToId: input.versionId,
      });
      if (input.operationId) {
        const pending = asset.pending_application_json ? JSON.parse(asset.pending_application_json) : null;
        if (pending?.operationId !== input.operationId) throw new Error("Skill application intent changed");
        await tx.exec('UPDATE ce_skill_assets SET pending_application_json = NULL WHERE asset_id = ?', [input.assetId]);
      }
      return (await tx.query<SkillVersionRow>(`${versionProjection} WHERE v.version_id = ?`, [input.versionId]))[0];
    });
  }

  async createManualVersion(input: {
    versionId: string;
    assetId: string;
    baseVersionId: string;
    packageRef: string;
    packageSha256: string;
    creationKind: "edit" | "upload" | "rollback";
    sourceVersionId: string;
    createdBy: string;
    operationId?: string;
  }): Promise<SkillVersionRow> {
    return skillAuditTransaction(this.db, async (tx) => {
      await tx.exec('UPDATE ce_skill_assets SET asset_id = asset_id WHERE asset_id = ?', [input.assetId]);
      const existing = (await tx.query<SkillVersionRow>(`${versionProjection} WHERE v.version_id = ? AND v.asset_id = ?`, [input.versionId, input.assetId]))[0];
      if (existing) return existing;
      const asset = (await tx.query<SkillAssetRow>(`${skillAssetProjection} WHERE a.asset_id = ?`, [input.assetId]))[0];
      if (!asset) throw new Error("Skill 不存在");
      const base = (await tx.query<SkillVersionRow>(
        `${versionProjection} WHERE v.asset_id = ? AND v.version_id = ?`,
        [input.assetId, input.baseVersionId],
      ))[0];
      if (!base || Number(base.version_no) !== Number(asset.current_version_no)) {
        throw Object.assign(new Error("Skill 已产生新版本，请刷新后重试"), { code: "SKILL_VERSION_CONFLICT" });
      }
      const versionNo = Number(asset.current_version_no) + 1;
      const now = tx.dialect.now();
      await tx.exec(
        `INSERT INTO ce_skill_versions
         (version_id, asset_id, version_no, package_ref, package_sha256,
          baseline_package_ref, baseline_package_sha256, creation_kind,
          source_version_id, created_by, status, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)`,
        [input.versionId, input.assetId, versionNo, input.packageRef, input.packageSha256,
          base.package_ref, base.package_sha256, input.creationKind,
          input.sourceVersionId, input.createdBy, now],
      );
      await tx.exec(
        `UPDATE ce_skill_assets SET current_version_no = ?, gmt_modified = ? WHERE asset_id = ?`,
        [versionNo, now, input.assetId],
      );
      if (input.operationId) {
        const pending = asset.pending_application_json ? JSON.parse(asset.pending_application_json) : null;
        if (pending?.operationId !== input.operationId) throw new Error("Skill application intent changed");
        await tx.exec('UPDATE ce_skill_assets SET pending_application_json = NULL WHERE asset_id = ?', [input.assetId]);
      }
      return (await tx.query<SkillVersionRow>(`${versionProjection} WHERE v.version_id = ?`, [input.versionId]))[0];
    });
  }

  async listVersions(assetId: string): Promise<SkillVersionRow[]> {
    return this.db.query<SkillVersionRow>(
      `${versionProjection} WHERE v.asset_id = ? ORDER BY v.version_no DESC`,
      [assetId],
    );
  }

  async findVersion(assetId: string, versionId: string): Promise<SkillVersionRow | null> {
    return (await this.db.query<SkillVersionRow>(
      `${versionProjection} WHERE v.asset_id = ? AND v.version_id = ?`,
      [assetId, versionId],
    ))[0] ?? null;
  }

  async findVersionByNumber(assetId: string, versionNo: number): Promise<SkillVersionRow | null> {
    return (await this.db.query<SkillVersionRow>(
      `${versionProjection} WHERE v.asset_id = ? AND v.version_no = ?`,
      [assetId, versionNo],
    ))[0] ?? null;
  }

  async findVersionBySourceTask(assetId: string, sourceTaskId: string): Promise<SkillVersionRow | null> {
    return (await this.db.query<SkillVersionRow>(
      `${versionProjection} WHERE v.asset_id = ? AND v.source_task_id = ?`,
      [assetId, sourceTaskId],
    ))[0] ?? null;
  }
}
