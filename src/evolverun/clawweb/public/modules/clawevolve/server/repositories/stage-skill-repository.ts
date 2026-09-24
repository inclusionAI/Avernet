import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { StageExtensionMode, StageKey } from "../services/evolve/stage-catalog.js";

export type StageDevelopmentRow = {
  stage_skill_id: string;
  owner_user_id: string;
  space_id: string | null;
  space_type: "PERSONAL" | "TEAM" | null;
  space_name: string | null;
  display_name: string;
  flow_key: "bot_evolution" | "skill_evolution";
  stage_key: StageKey;
  extension_mode: StageExtensionMode;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type StageSkillImplementationRow = {
  id: number;
  stage_skill_id: string;
  implementation_id: string;
  owner_user_id: string;
  space_id: string | null;
  space_type: "PERSONAL" | "TEAM" | null;
  space_name: string | null;
  display_name: string;
  stage_key: StageKey;
  extension_mode: StageExtensionMode;
  version_no: number;
  status: "validated" | "testing" | "test_passed" | "test_failed" | "registered" | "deleted";
  package_ref: string;
  package_sha256: string;
  static_validation_json: string;
  integration_test_task_id: string | null;
  integration_test_status: "untested" | "testing" | "test_passed" | "test_failed";
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type StageExtensionRunRow = {
  id: number;
  step_id: string;
  task_id: string;
  stage_key: StageKey;
  extension_mode: StageExtensionMode;
  implementation_id: string;
  initial_input_json: string | null;
  gmt_create: number | string;
};

export type StageInteractionRow = {
  id: number;
  interaction_id: string;
  task_id: string;
  step_id: string;
  attempt_no: number;
  status: "waiting" | "answered";
  request_json: string;
  response_json: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export class StageSkillRepository {
  constructor(private readonly db: IDatabase) {}

  async createDevelopment(input: {
    stageSkillId: string; ownerUserId: string; displayName: string;
    spaceId?: string | null; spaceType?: "PERSONAL" | "TEAM" | null; spaceName?: string | null;
    flow: StageDevelopmentRow["flow_key"]; stage: StageKey; mode: StageExtensionMode;
  }): Promise<StageDevelopmentRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO ce_stage_developments
       (stage_skill_id, owner_user_id, space_id, space_type, space_name, display_name, flow_key, stage_key, extension_mode, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [input.stageSkillId, input.ownerUserId, input.spaceId ?? null, input.spaceType ?? null, input.spaceName ?? null,
        input.displayName, input.flow, input.stage, input.mode, now, now],
    );
    const row = await this.findDevelopment(input.stageSkillId);
    if (!row) throw new Error("开发记录创建失败");
    return row;
  }

  async findDevelopment(stageSkillId: string): Promise<StageDevelopmentRow | null> {
    return (await this.db.query<StageDevelopmentRow>(
      "SELECT * FROM ce_stage_developments WHERE stage_skill_id = ?", [stageSkillId],
    ))[0] ?? null;
  }

  async listDevelopments(ownerUserId: string): Promise<StageDevelopmentRow[]> {
    return this.db.query<StageDevelopmentRow>(
      `SELECT d.* FROM ce_stage_developments d WHERE owner_user_id = ?
       AND (NOT EXISTS (SELECT 1 FROM ce_stage_skill_implementations i WHERE i.stage_skill_id = d.stage_skill_id)
         OR EXISTS (SELECT 1 FROM ce_stage_skill_implementations i WHERE i.stage_skill_id = d.stage_skill_id AND i.status <> 'deleted'))
       ORDER BY gmt_modified DESC`,
      [ownerUserId],
    );
  }

  async deleteDraft(stageSkillId: string, ownerUserId: string): Promise<boolean> {
    const result = await this.db.exec(
      `DELETE FROM ce_stage_developments WHERE stage_skill_id = ? AND owner_user_id = ?
       AND NOT EXISTS (SELECT 1 FROM ce_stage_skill_implementations WHERE stage_skill_id = ?)`,
      [stageSkillId, ownerUserId, stageSkillId],
    );
    return result.affectedRows === 1;
  }

  async nextVersion(stageSkillId: string): Promise<number> {
    const row = (await this.db.query<{ max_version: number | string | null }>(
      `SELECT MAX(version_no) AS max_version FROM ce_stage_skill_implementations
       WHERE stage_skill_id = ?`,
      [stageSkillId],
    ))[0];
    return Number(row?.max_version ?? 0) + 1;
  }

  async createImplementation(input: {
    stageSkillId: string;
    implementationId: string;
    ownerUserId: string;
    spaceId?: string | null;
    spaceType?: "PERSONAL" | "TEAM" | null;
    spaceName?: string | null;
    displayName: string;
    stage: StageKey;
    mode: StageExtensionMode;
    versionNo: number;
    packageRef: string;
    packageSha256: string;
    staticValidation: unknown;
  }): Promise<StageSkillImplementationRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO ce_stage_skill_implementations
       (implementation_id, owner_user_id, space_id, space_type, space_name, display_name, stage_key, extension_mode, version_no,
        stage_skill_id, status, package_ref, package_sha256, static_validation_json, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'validated', ?, ?, ?, ?, ?)`,
      [input.implementationId, input.ownerUserId, input.spaceId ?? null, input.spaceType ?? null, input.spaceName ?? null,
        input.displayName, input.stage, input.mode,
        input.versionNo, input.stageSkillId,
        input.packageRef, input.packageSha256,
        JSON.stringify(input.staticValidation), now, now],
    );
    const created = await this.findImplementation(input.implementationId);
    if (!created) throw new Error("Stage Skill 记录创建失败");
    return created;
  }

  async findImplementation(implementationId: string): Promise<StageSkillImplementationRow | null> {
    return (await this.db.query<StageSkillImplementationRow>(
      "SELECT * FROM ce_stage_skill_implementations WHERE implementation_id = ?",
      [implementationId],
    ))[0] ?? null;
  }

  async listImplementations(ownerUserId: string, teamSpaceIds: readonly string[] = []): Promise<StageSkillImplementationRow[]> {
    return this.db.query<StageSkillImplementationRow>(
      `SELECT * FROM ce_stage_skill_implementations WHERE (owner_user_id = ?${teamSpaceIds.length ? ` OR (space_type = 'TEAM' AND space_id IN (${teamSpaceIds.map(() => "?").join(",")}))` : ""}) AND status <> 'deleted'
       ORDER BY stage_key, extension_mode, version_no DESC, id DESC`,
      [ownerUserId, ...teamSpaceIds],
    );
  }

  async findLatestStageSkill(stageSkillId: string): Promise<StageSkillImplementationRow | null> {
    return (await this.db.query<StageSkillImplementationRow>(
      `SELECT * FROM ce_stage_skill_implementations
       WHERE stage_skill_id = ? ORDER BY version_no DESC, id DESC LIMIT 1`,
      [stageSkillId],
    ))[0] ?? null;
  }

  async updateIntegrationTest(
    implementationId: string,
    taskId: string,
    status: "testing" | "test_passed" | "test_failed",
  ): Promise<void> {
    await this.db.exec(
      `UPDATE ce_stage_skill_implementations
       SET integration_test_task_id = ?, integration_test_status = ?,
           status = CASE WHEN status = 'registered' THEN status ELSE ? END,
           gmt_modified = ?
       WHERE implementation_id = ?`,
      [taskId, status, status, this.db.dialect.now(), implementationId],
    );
  }

  async registerImplementation(implementationId: string): Promise<StageSkillImplementationRow | null> {
    await this.db.exec(
      `UPDATE ce_stage_skill_implementations SET status = 'registered', gmt_modified = ?
       WHERE implementation_id = ? AND status IN ('validated', 'test_passed')`,
      [this.db.dialect.now(), implementationId],
    );
    return this.findImplementation(implementationId);
  }

  async deleteImplementation(implementationId: string, ownerUserId: string): Promise<boolean> {
    const current = await this.findImplementation(implementationId);
    if (!current || current.owner_user_id !== ownerUserId || current.status === "deleted") return false;
    const result = await this.db.exec(
      `UPDATE ce_stage_skill_implementations SET status = 'deleted', gmt_modified = ?
       WHERE implementation_id = ? AND owner_user_id = ? AND status <> 'deleted'`,
      [this.db.dialect.now(), implementationId, ownerUserId],
    );
    return result.affectedRows === 1;
  }

  async createExtensionRun(input: {
    stepId: string;
    taskId: string;
    stage: StageKey;
    mode: StageExtensionMode;
    implementationId: string;
    initialInput?: unknown;
  }): Promise<void> {
    await this.db.exec(
      `INSERT INTO ce_stage_extension_runs
       (step_id, task_id, stage_key, extension_mode, implementation_id, initial_input_json)
       VALUES (?, ?, ?, ?, ?, ?)`,
      [input.stepId, input.taskId, input.stage, input.mode, input.implementationId,
        input.initialInput === undefined ? null : JSON.stringify(input.initialInput)],
    );
  }

  async findExtensionRun(stepId: string): Promise<StageExtensionRunRow | null> {
    return (await this.db.query<StageExtensionRunRow>(
      "SELECT * FROM ce_stage_extension_runs WHERE step_id = ?",
      [stepId],
    ))[0] ?? null;
  }

  async listExtensionRuns(taskId: string): Promise<StageExtensionRunRow[]> {
    return this.db.query<StageExtensionRunRow>(
      "SELECT * FROM ce_stage_extension_runs WHERE task_id = ? ORDER BY id ASC",
      [taskId],
    );
  }

  async createInteraction(input: {
    interactionId: string;
    taskId: string;
    stepId: string;
    request: unknown;
  }): Promise<StageInteractionRow> {
    const last = (await this.db.query<{ max_attempt: number | string | null }>(
      "SELECT MAX(attempt_no) AS max_attempt FROM ce_stage_interactions WHERE step_id = ?",
      [input.stepId],
    ))[0];
    const attempt = Number(last?.max_attempt ?? 0) + 1;
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO ce_stage_interactions
       (interaction_id, task_id, step_id, attempt_no, status, request_json, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, 'waiting', ?, ?, ?)`,
      [input.interactionId, input.taskId, input.stepId, attempt, JSON.stringify(input.request), now, now],
    );
    return (await this.db.query<StageInteractionRow>(
      "SELECT * FROM ce_stage_interactions WHERE interaction_id = ?",
      [input.interactionId],
    ))[0];
  }

  async findWaitingInteraction(taskId: string, stepId: string): Promise<StageInteractionRow | null> {
    return (await this.db.query<StageInteractionRow>(
      `SELECT * FROM ce_stage_interactions
       WHERE task_id = ? AND step_id = ? AND status = 'waiting'
       ORDER BY attempt_no DESC LIMIT 1`,
      [taskId, stepId],
    ))[0] ?? null;
  }

  async findInteraction(interactionId: string): Promise<StageInteractionRow | null> {
    return (await this.db.query<StageInteractionRow>(
      "SELECT * FROM ce_stage_interactions WHERE interaction_id = ?",
      [interactionId],
    ))[0] ?? null;
  }

  async findLatestAnsweredInteraction(taskId: string, stepId: string): Promise<StageInteractionRow | null> {
    return (await this.db.query<StageInteractionRow>(
      `SELECT * FROM ce_stage_interactions
       WHERE task_id = ? AND step_id = ? AND status = 'answered'
       ORDER BY attempt_no DESC LIMIT 1`,
      [taskId, stepId],
    ))[0] ?? null;
  }

  async listInteractions(taskId: string): Promise<StageInteractionRow[]> {
    return this.db.query<StageInteractionRow>(
      "SELECT * FROM ce_stage_interactions WHERE task_id = ? ORDER BY id ASC",
      [taskId],
    );
  }

  async answerInteraction(interactionId: string, response: unknown): Promise<boolean> {
    const result = await this.db.exec(
      `UPDATE ce_stage_interactions SET status = 'answered', response_json = ?, gmt_modified = ?
       WHERE interaction_id = ? AND status = 'waiting'`,
      [JSON.stringify(response), this.db.dialect.now(), interactionId],
    );
    return result.affectedRows === 1;
  }
}
