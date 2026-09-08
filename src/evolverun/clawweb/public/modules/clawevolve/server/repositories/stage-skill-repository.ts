import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { StageExtensionMode, StageKey } from "../services/evolve/stage-catalog.js";

export type StageSkillImplementationRow = {
  id: number;
  stage_skill_id: string;
  implementation_id: string;
  owner_user_id: string;
  display_name: string;
  stage_key: StageKey;
  extension_mode: StageExtensionMode;
  version_no: number;
  status: "validated" | "testing" | "test_passed" | "test_failed" | "registered" | "deleted";
  package_ref: string;
  package_sha256: string;
  static_validation_json: string;
  integration_test_task_id: string | null;
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
       (implementation_id, owner_user_id, display_name, stage_key, extension_mode, version_no,
        stage_skill_id, status, package_ref, package_sha256, static_validation_json, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'validated', ?, ?, ?, ?, ?)`,
      [input.implementationId, input.ownerUserId, input.displayName, input.stage, input.mode,
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

  async listImplementations(ownerUserId: string): Promise<StageSkillImplementationRow[]> {
    return this.db.query<StageSkillImplementationRow>(
      `SELECT * FROM ce_stage_skill_implementations WHERE owner_user_id = ? AND status <> 'deleted'
       ORDER BY stage_key, extension_mode, version_no DESC, id DESC`,
      [ownerUserId],
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
       SET integration_test_task_id = ?, status = ?, gmt_modified = ?
       WHERE implementation_id = ?`,
      [taskId, status, this.db.dialect.now(), implementationId],
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
