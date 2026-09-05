import { randomUUID } from "node:crypto";
import type { IDatabase } from "../db.js";
import { nowForDb } from "../db.js";

export type EvolveTaskRow = {
  id: number; task_id: string; task_type: string; user_id: string; bot_id: string;
  task_name: string | null; remark: string | null;
  status: string; config_json: string; error_message: string | null; created_by: string;
  gmt_create: number; gmt_modified: number;
  bot_name?: string | null;
};

export type EvolvePackRow = {
  id: number; pack_id: string; user_id: string; bot_id: string;
  source_task_id: string; source_step_id: string; source_kind: "baseline" | "snapshot" | "round";
  source_round: number; artifact_ref: string; artifact_size: number;
  artifact_sha256: string; artifact_content_type: string; status: string;
  gmt_create: number | string; gmt_modified: number | string;
};

export type EvolveLessonRow = {
  id: number;
  lesson_id: string;
  workflow_id: string | null;
  node_id: string | null;
  failure_signature: string;
  failure_mode: string;
  executor_type: string | null;
  fix_kind: string;
  fix_spec: string;
  status: string;
  confidence: number | string;
  hit_count: number;
  rescued_count: number;
  note: string | null;
  created_by: string | null;
  updated_by: string | null;
  source: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type EvolveRunDiagnosisRow = {
  id: number;
  diagnosis_id: string;
  flow_id: string;
  workflow_id: string;
  run_id: string | null;
  node_id: string | null;
  failure_signature: string;
  failure_mode: string;
  executor_type: string | null;
  weak_node_id: string | null;
  suggested_fix_kind: string | null;
  lesson_id_hit: string | null;
  error_text: string | null;
  created_by: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type EvolveLessonOutcomeRow = {
  id: number;
  outcome_id: string;
  lesson_id: string | null;
  suggestion_id: string | null;
  source_task_id: string | null;
  source_step_id: string | null;
  workflow_id: string | null;
  node_id: string | null;
  action: string;
  applied: number;
  succeeded: number;
  verdict: string;
  note: string | null;
  created_by: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type CreateSuggestionOutcomeInput = {
  outcomeId?: string;
  suggestionId: string;
  workflowId?: string | null;
  nodeId?: string | null;
  action: string;
  applied?: boolean;
  succeeded?: boolean;
  verdict?: string;
  note?: string | null;
  sourceTaskId?: string | null;
  sourceStepId?: string | null;
  createdBy?: string | null;
};

export type EvolveSuggestionActionRow = {
  id: number;
  signature: string;
  workflow_id: string;
  node_id: string | null;
  action: string;
  fix_kind: string | null;
  note: string | null;
  created_by: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type EvolveSuggestionRow = {
  id: number;
  workflow_id: string;
  node_id: string | null;
  weak_node_id: string | null;
  failure_signature: string;
  failure_mode: string | null;
  fix_kind: string | null;
  fix_spec: string | null;
  source_diagnosis_ids: string | null;
  impact_run_ids: string | null;
  status: string;
  applied_at: number | null;
  verification_status: string;
  verification_checked_at: number | null;
  recurrence_count: number;
  last_recurrence_at: number | null;
  action_log: string | null;
  created_by: string | null;
  updated_by: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
  proposal_json?: string | null;
  proposal_digest?: string | null;
  apply_task_id?: string | null;
};

export type CreateSuggestionInput = {
  suggestionId?: string;
  workflowId: string;
  nodeId?: string | null;
  weakNodeId?: string | null;
  failureSignature: string;
  failureMode?: string | null;
  fixKind?: string | null;
  fixSpec?: string | null;
  sourceDiagnosisIds?: string[];
  impactRunIds?: string[];
  status?: string;
  createdBy?: string | null;
  updatedBy?: string | null;
};

export type UpdateSuggestionInput = {
  nodeId?: string | null;
  weakNodeId?: string | null;
  failureMode?: string | null;
  fixKind?: string | null;
  fixSpec?: string | null;
  sourceDiagnosisIds?: string[];
  impactRunIds?: string[];
  status?: string;
  actionLog?: unknown[];
  updatedBy?: string | null;
};

export type SuggestionActionInput = {
  workflowId: string;
  signature: string;
  action: string;
  nodeId?: string | null;
  fixKind?: string | null;
  note?: string | null;
  createdBy?: string | null;
};

export type EvolveTaskLogArchiveRow = {
  id: number; archive_id: string; task_id: string; active_key: string | null; status: string; requested_by: string;
  transport: string | null; bot_run_id: string | null; bot_session_id: string | null;
  platform_response_json: string | null; artifact_ref: string | null; artifact_size: number | null;
  artifact_sha256: string | null; artifact_content_type: string | null; metadata_json: string | null;
  error_code: string | null; error_message: string | null;
  started_at: number | string | null; completed_at: number | string | null;
  gmt_create: number | string; gmt_modified: number | string;
};

export type EvolveTaskPageQuery = {
  createdBy: string | null;
  ownerUserId?: string | null;
  page: number;
  pageSize: number;
  taskTypes?: string[];
  excludedTaskTypes?: string[];
  statuses?: string[];
  query?: string;
};

export type EvolveStepRow = {
  id: number; step_id: string; task_id: string; step_type: string; step_no: number;
  round_no: number | null; command: string;
  status: string; bot_run_id: string | null;
  bot_session_id: string | null; bot_response_json: string | null;
  output_json: string | null; summary: string | null;
  error_code: string | null; error_message: string | null; retryable: number | null;
  started_at: number | string | null; completed_at: number | string | null;
  gmt_create: number | string; gmt_modified: number | string;
};

export type EvolveOptimizeVersionRow = {
  step_id: string;
  task_id: string;
  round_no: number | null;
  output_json: string | null;
  completed_at: number | string | null;
  gmt_modified: number | string;
  owner_user_id: string;
  source_bot_id: string;
  source_task_name: string | null;
  source_task_type: string;
};

export type EvolveBotRuntime = {
  activeEngine: string | null;
  botType: string | null;
  hasServiceBot: boolean;
  botStatus: string | null;
  bindingId: string | number | null;
  provider: string | null;
  deviceId: string | null;
  /** Raw ARCA PaaS device id. It may include the stable `@template_id` suffix. */
  arcaInstanceId?: string | null;
  bindingStatus: string | null;
  env: string | null;
  ownerId?: string;
  accessType?: "owner" | "collaborator";
};

export type EvolveBotOption = {
  botId: string; botName: string | null; env: string | null;
  activeEngine: string | null; botType: string | null;
  ownerId?: string | null; accessType?: "owner" | "collaborator";
};

export type AccessibleEvolveBotRuntime = {
  runtime: EvolveBotRuntime;
  ownerId: string;
  accessType: "owner" | "collaborator";
};

export class EvolveRepository {
  constructor(private readonly db: IDatabase) {}

  async createTaskLogArchive(input: { archiveId: string; taskId: string; requestedBy: string }): Promise<EvolveTaskLogArchiveRow> {
    // ce_task_log_archives deliberately stores portable unix seconds in BIGINT
    // columns. Do not use dialect.now(): MySQL/ZDAS returns a TIMESTAMP string,
    // which is rejected by the production BIGINT schema before dispatch starts.
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `INSERT INTO ce_task_log_archives
       (archive_id, task_id, active_key, status, requested_by, gmt_create, gmt_modified)
       VALUES (?, ?, ?, 'dispatching', ?, ?, ?)`,
      [input.archiveId, input.taskId, input.taskId, input.requestedBy, now, now],
    );
    const row = await this.findTaskLogArchive(input.taskId, input.archiveId);
    if (!row) throw new Error("日志归档记录创建失败");
    return row;
  }

  async findTaskLogArchive(taskId: string, archiveId: string): Promise<EvolveTaskLogArchiveRow | null> {
    return (await this.db.query<EvolveTaskLogArchiveRow>(
      "SELECT * FROM ce_task_log_archives WHERE task_id = ? AND archive_id = ?",
      [taskId, archiveId],
    ))[0] ?? null;
  }

  async findActiveTaskLogArchive(taskId: string): Promise<EvolveTaskLogArchiveRow | null> {
    return (await this.db.query<EvolveTaskLogArchiveRow>(
      `SELECT * FROM ce_task_log_archives
       WHERE task_id = ? AND status IN ('dispatching', 'running')
       ORDER BY id DESC LIMIT 1`,
      [taskId],
    ))[0] ?? null;
  }

  async listTaskLogArchives(taskId: string, limit = 20): Promise<EvolveTaskLogArchiveRow[]> {
    const bounded = Math.max(1, Math.min(100, Math.trunc(limit)));
    return this.db.query<EvolveTaskLogArchiveRow>(
      `SELECT * FROM ce_task_log_archives WHERE task_id = ? ORDER BY id DESC LIMIT ${bounded}`,
      [taskId],
    );
  }

  async markTaskLogArchiveDispatched(input: {
    taskId: string; archiveId: string; transport: string; runId: string | null;
    sessionId: string | null; platformResponse: unknown;
  }): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE ce_task_log_archives SET status = 'running', transport = ?, bot_run_id = ?,
       bot_session_id = ?, platform_response_json = ?, started_at = COALESCE(started_at, ?), gmt_modified = ?
       WHERE task_id = ? AND archive_id = ? AND status NOT IN ('succeeded', 'failed')`,
      [input.transport, input.runId, input.sessionId, JSON.stringify(input.platformResponse ?? null),
        now, now, input.taskId, input.archiveId],
    );
  }

  async reportTaskLogArchive(input: {
    taskId: string; archiveId: string; status: 'running' | 'succeeded' | 'failed';
    artifact?: { ref: string; size: number; sha256: string; contentType: string } | null;
    metadata?: unknown; errorCode?: string | null; errorMessage?: string | null;
  }): Promise<void> {
    const terminal = input.status === 'succeeded' || input.status === 'failed';
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE ce_task_log_archives SET status = ?,
       artifact_ref = COALESCE(?, artifact_ref), artifact_size = COALESCE(?, artifact_size),
       artifact_sha256 = COALESCE(?, artifact_sha256), artifact_content_type = COALESCE(?, artifact_content_type),
       metadata_json = COALESCE(?, metadata_json), error_code = ?, error_message = ?,
       active_key = CASE WHEN ? THEN NULL ELSE active_key END,
       started_at = COALESCE(started_at, ?), completed_at = CASE WHEN ? THEN ? ELSE completed_at END,
       gmt_modified = ? WHERE task_id = ? AND archive_id = ? AND status NOT IN ('succeeded', 'failed')`,
      [input.status, input.artifact?.ref ?? null, input.artifact?.size ?? null,
        input.artifact?.sha256 ?? null, input.artifact?.contentType ?? null,
        input.metadata == null ? null : JSON.stringify(input.metadata), input.errorCode ?? null,
        input.errorMessage ?? null, terminal ? 1 : 0, now, terminal ? 1 : 0,
        now, now, input.taskId, input.archiveId],
    );
  }

  async createTask(input: {
    taskId: string; taskType: string; userId: string; botId: string;
    taskName: string; remark?: string | null; configJson: string; createdBy: string;
  }): Promise<void> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO ce_tasks
       (task_id, task_type, task_name, remark, user_id, bot_id, status, config_json, created_by, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)`,
      [input.taskId, input.taskType, input.taskName, input.remark ?? null, input.userId, input.botId,
        input.configJson, input.createdBy, now, now],
    );
  }

  async createTaskWithStep(input: {
    task: {
      taskId: string; taskType: string; userId: string; botId: string;
      taskName: string; remark?: string | null; configJson: string; createdBy: string;
    };
    step: {
      stepId: string; stepType: string; stepNo: number;
      roundNo?: number | null; command: string;
    };
  }): Promise<void> {
    await this.db.transaction(async (tx) => {
      const now = tx.dialect.now();
      await tx.exec(
        `INSERT INTO ce_tasks
         (task_id, task_type, task_name, remark, user_id, bot_id, status, config_json, created_by, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)`,
        [input.task.taskId, input.task.taskType, input.task.taskName, input.task.remark ?? null,
          input.task.userId, input.task.botId, input.task.configJson, input.task.createdBy, now, now],
      );
      await tx.exec(
        `INSERT INTO ce_steps
         (step_id, task_id, step_type, step_no, round_no, command, status, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?)`,
        [input.step.stepId, input.task.taskId, input.step.stepType, input.step.stepNo,
          input.step.roundNo ?? null, input.step.command, now, now],
      );
    });
  }

  async registerPack(input: Omit<EvolvePackRow, "id" | "gmt_create" | "gmt_modified" | "status">): Promise<EvolvePackRow> {
    const onConflict = this.db.dbType === "mysql" || this.db.dbType === "zdas"
      ? "ON DUPLICATE KEY UPDATE pack_id = pack_id"
      : "ON CONFLICT DO NOTHING";
    await this.db.exec(
      `INSERT INTO ce_packs
       (pack_id, user_id, bot_id, source_task_id, source_step_id, source_kind, source_round,
        artifact_ref, artifact_size, artifact_sha256, artifact_content_type, status)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available')
       ${onConflict}`,
      [input.pack_id, input.user_id, input.bot_id, input.source_task_id, input.source_step_id,
        input.source_kind, input.source_round, input.artifact_ref, input.artifact_size,
        input.artifact_sha256, input.artifact_content_type],
    );
    const row = (await this.db.query<EvolvePackRow>(
      `SELECT * FROM ce_packs WHERE source_task_id = ? AND source_step_id = ? AND source_kind = ? AND source_round = ?`,
      [input.source_task_id, input.source_step_id, input.source_kind, input.source_round],
    ))[0];
    if (!row) throw new Error("Pack 登记失败");
    if (row.artifact_ref !== input.artifact_ref || row.artifact_sha256 !== input.artifact_sha256) {
      throw new Error("同一来源 Step 上报了不同的 Pack");
    }
    return row;
  }

  async findPack(packId: string): Promise<EvolvePackRow | null> {
    return (await this.db.query<EvolvePackRow>("SELECT * FROM ce_packs WHERE pack_id = ?", [packId]))[0] ?? null;
  }

  async listPacks(userId: string | null, botId?: string): Promise<EvolvePackRow[]> {
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (userId) { clauses.push("p.user_id = ?"); params.push(userId); }
    if (botId) { clauses.push("p.bot_id = ?"); params.push(botId); }
    return this.db.query<EvolvePackRow>(
      `SELECT p.* FROM ce_packs p
       ${clauses.length ? `WHERE ${clauses.join(" AND ")}` : ""}
       ORDER BY p.gmt_create DESC, p.id DESC`,
      params,
    );
  }

  async listCompletedOptimizeVersions(userId: string | null, botId?: string): Promise<EvolveOptimizeVersionRow[]> {
    const clauses = ["s.step_type = 'optimize'", "s.status IN ('succeeded', 'completed')"];
    const params: unknown[] = [];
    if (userId) { clauses.push("t.user_id = ?"); params.push(userId); }
    if (botId) { clauses.push("t.bot_id = ?"); params.push(botId); }
    return this.db.query<EvolveOptimizeVersionRow>(
      `SELECT s.step_id, s.task_id, s.round_no, s.output_json, s.completed_at, s.gmt_modified,
              t.user_id AS owner_user_id, t.bot_id AS source_bot_id,
              t.task_name AS source_task_name, t.task_type AS source_task_type
       FROM ce_steps s
       INNER JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE ${clauses.join(" AND ")}
       ORDER BY COALESCE(s.completed_at, s.gmt_modified) DESC, s.id DESC`,
      params,
    );
  }

  async listEvolveOwnerUserIds(): Promise<string[]> {
    const rows = await this.db.query<{ owner_user_id: string }>(
      `SELECT owner_user_id FROM (
         SELECT user_id AS owner_user_id FROM ce_tasks
         UNION SELECT user_id AS owner_user_id FROM ce_packs
         UNION SELECT owner_user_id FROM cm_bench_domains
       ) owners WHERE owner_user_id IS NOT NULL AND owner_user_id <> '' ORDER BY owner_user_id`,
    );
    return rows.map((row) => String(row.owner_user_id));
  }

  async listPackApplications(pack: EvolvePackRow): Promise<EvolveTaskRow[]> {
    const tasks = await this.db.query<EvolveTaskRow>(
      "SELECT * FROM ce_tasks WHERE task_type = 'pack_restore' AND user_id = ? AND bot_id = ? ORDER BY gmt_create DESC",
      [pack.user_id, pack.bot_id],
    );
    return tasks.filter((task) => this.taskUsesPack(task, pack));
  }

  async countPackApplications(packs: EvolvePackRow[]): Promise<Record<string, number>> {
    if (packs.length === 0) return {};
    const ownerUserIds = [...new Set(packs.map((pack) => pack.user_id))];
    const tasks = await this.db.query<EvolveTaskRow>(
      `SELECT * FROM ce_tasks WHERE task_type = 'pack_restore' AND user_id IN (${ownerUserIds.map(() => "?").join(",")})`,
      ownerUserIds,
    );
    return Object.fromEntries(packs.map((pack) => [
      pack.pack_id, tasks.filter((task) => task.bot_id === pack.bot_id && this.taskUsesPack(task, pack)).length,
    ]));
  }

  async listLessons(options: {
    workflowId?: string | null;
    status?: string | null;
    query?: string | null;
    limit?: number;
    offset?: number;
  } = {}): Promise<{ rows: EvolveLessonRow[]; total: number }> {
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (options.workflowId?.trim()) {
      clauses.push("workflow_id = ?");
      params.push(options.workflowId.trim());
    }
    if (options.status?.trim()) {
      clauses.push("status = ?");
      params.push(options.status.trim());
    }
    const query = options.query?.trim();
    if (query) {
      const like = `%${query}%`;
      clauses.push(`(
        lesson_id LIKE ? OR workflow_id LIKE ? OR node_id LIKE ? OR
        failure_signature LIKE ? OR failure_mode LIKE ? OR executor_type LIKE ? OR
        fix_kind LIKE ? OR fix_spec LIKE ? OR note LIKE ?
      )`);
      params.push(like, like, like, like, like, like, like, like, like);
    }
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
    const totalRow = (await this.db.query<{ total: number }>(`SELECT COUNT(*) AS total FROM workflow_healing_lessons ${where}`, params))[0];
    const limit = Math.min(Math.max(Number(options.limit ?? 50), 1), 200);
    const offset = Math.max(Number(options.offset ?? 0), 0);
    const rows = await this.db.query<EvolveLessonRow>(
      `SELECT * FROM workflow_healing_lessons ${where} ORDER BY gmt_create DESC, id DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );
    return { rows, total: Number(totalRow?.total ?? rows.length) };
  }

  async findLesson(lessonId: string): Promise<EvolveLessonRow | null> {
    return (await this.db.query<EvolveLessonRow>("SELECT * FROM workflow_healing_lessons WHERE lesson_id = ?", [lessonId]))[0] ?? null;
  }

  async createLesson(input: {
    lessonId: string;
    workflowId?: string | null;
    nodeId?: string | null;
    failureSignature: string;
    failureMode: string;
    executorType?: string | null;
    fixKind: string;
    fixSpec: string;
    status?: string;
    confidence?: number;
    hitCount?: number;
    rescuedCount?: number;
    note?: string | null;
    source?: string | null;
    createdBy?: string | null;
    updatedBy?: string | null;
  }): Promise<EvolveLessonRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO workflow_healing_lessons
       (lesson_id, workflow_id, node_id, failure_signature, failure_mode, executor_type, fix_kind, fix_spec, status, confidence, hit_count, rescued_count, note, source, created_by, updated_by, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.lessonId, input.workflowId ?? null, input.nodeId ?? null, input.failureSignature, input.failureMode,
        input.executorType ?? null, input.fixKind, input.fixSpec, input.status ?? "draft",
        input.confidence ?? 0, input.hitCount ?? 0, input.rescuedCount ?? 0, input.note ?? null,
        input.source ?? "log_analysis", input.createdBy ?? null, input.updatedBy ?? input.createdBy ?? null, now, now,
      ],
    );
    const lesson = await this.findLesson(input.lessonId);
    if (!lesson) throw new Error("创建 lesson 失败");
    return lesson;
  }

  async updateLesson(lessonId: string, patch: {
    workflowId?: string | null;
    nodeId?: string | null;
    failureSignature?: string;
    failureMode?: string;
    executorType?: string | null;
    fixKind?: string;
    fixSpec?: string;
    status?: string;
    confidence?: number;
    note?: string | null;
    updatedBy?: string | null;
  }): Promise<EvolveLessonRow | null> {
    const current = await this.findLesson(lessonId);
    if (!current) return null;
    const sets: string[] = [];
    const params: unknown[] = [];
    const push = (sql: string, value: unknown) => { sets.push(sql); params.push(value); };
    if (patch.workflowId !== undefined) push("workflow_id = ?", patch.workflowId ?? null);
    if (patch.nodeId !== undefined) push("node_id = ?", patch.nodeId ?? null);
    if (patch.failureSignature !== undefined) push("failure_signature = ?", patch.failureSignature);
    if (patch.failureMode !== undefined) push("failure_mode = ?", patch.failureMode);
    if (patch.executorType !== undefined) push("executor_type = ?", patch.executorType ?? null);
    if (patch.fixKind !== undefined) push("fix_kind = ?", patch.fixKind);
    if (patch.fixSpec !== undefined) push("fix_spec = ?", patch.fixSpec);
    if (patch.status !== undefined) push("status = ?", patch.status);
    if (patch.confidence !== undefined) push("confidence = ?", patch.confidence);
    if (patch.note !== undefined) push("note = ?", patch.note ?? null);
    if (patch.updatedBy !== undefined) push("updated_by = ?", patch.updatedBy ?? null);
    if (sets.length === 0) return current;
    sets.push("gmt_modified = ?");
    params.push(this.db.dialect.now());
    params.push(lessonId);
    await this.db.exec(`UPDATE workflow_healing_lessons SET ${sets.join(", ")} WHERE lesson_id = ?`, params);
    return await this.findLesson(lessonId);
  }

  async recordLessonOutcome(input: {
    outcomeId: string;
    lessonId: string;
    workflowId?: string | null;
    nodeId?: string | null;
    action: string;
    applied?: boolean;
    succeeded?: boolean;
    verdict?: string;
    note?: string | null;
    createdBy?: string | null;
  }): Promise<EvolveLessonOutcomeRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO workflow_healing_outcomes
       (outcome_id, lesson_id, workflow_id, node_id, action, applied, succeeded, verdict, note, created_by, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.outcomeId, input.lessonId, input.workflowId ?? null, input.nodeId ?? null, input.action,
        input.applied ? 1 : 0, input.succeeded ? 1 : 0, input.verdict ?? "neutral", input.note ?? null,
        input.createdBy ?? null, now, now,
      ],
    );
    await this.db.exec(
      `UPDATE workflow_healing_lessons
       SET hit_count = hit_count + ?, rescued_count = rescued_count + ?, updated_by = ?, gmt_modified = ?
       WHERE lesson_id = ?`,
      [input.applied ? 1 : 0, input.applied && input.succeeded ? 1 : 0, input.createdBy ?? null, now, input.lessonId],
    );
    const row = (await this.db.query<EvolveLessonOutcomeRow>("SELECT * FROM workflow_healing_outcomes WHERE outcome_id = ?", [input.outcomeId]))[0];
    if (!row) throw new Error("记录 outcome 失败");
    return row;
  }

  async recordSuggestionOutcome(input: CreateSuggestionOutcomeInput): Promise<EvolveLessonOutcomeRow> {
    const now = this.db.dialect.now();
    const outcomeId = input.outcomeId ?? `OUT-SUGG-${randomUUID().slice(0, 12).toUpperCase()}`;
    await this.db.exec(
      `INSERT INTO workflow_healing_outcomes
       (outcome_id, lesson_id, suggestion_id, workflow_id, node_id, action, applied, succeeded, verdict, note, source_task_id, source_step_id, created_by, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        outcomeId, null, input.suggestionId, input.workflowId ?? null, input.nodeId ?? null, input.action,
        input.applied ? 1 : 0, input.succeeded ? 1 : 0, input.verdict ?? "neutral", input.note ?? null,
        input.sourceTaskId ?? null, input.sourceStepId ?? null, input.createdBy ?? null, now, now,
      ],
    );
    const row = (await this.db.query<EvolveLessonOutcomeRow>("SELECT * FROM workflow_healing_outcomes WHERE outcome_id = ?", [outcomeId]))[0];
    if (!row) throw new Error("记录 suggestion outcome 失败");
    return row;
  }

  
  async listEligibleBotsForSuggestion(userId: string, workflowId: string): Promise<{ botId: string; botName: string | null; env: string | null; accessType: string; ownerId: string | null }[]> {
    // Suggestion application executes on one concrete Bot. Owner/global grants
    // authorize the web user, but never make every owned Bot an execution target.
    const botRows = await this.db.query<{ bot_id: string; bot_owner_id: string }>(
      `SELECT bot_id, bot_owner_id FROM bot_workflow_permissions
       WHERE workflow_id = ? AND can_edit = 1 AND bot_id IS NOT NULL AND bot_id <> ''
         AND bot_owner_id = ?
         AND id IN (
           SELECT MAX(id) FROM bot_workflow_permissions
           WHERE workflow_id = ? AND bot_owner_id = ?
           GROUP BY bot_id
         )`,
      [workflowId, userId, workflowId, userId],
    );

    const result: { botId: string; botName: string | null; env: string | null; accessType: string; ownerId: string | null }[] = [];
    const seen = new Set<string>();
    for (const r of botRows) {
      const info = await this.db.query<{ bot_id: string; bot_name: string | null; env: string | null }>(
        `SELECT bot_id, bot_name, env FROM ac_bots
         WHERE bot_id = ? AND (owner_id = ? OR entity_id = ?) AND is_delete = 0
         ORDER BY id DESC LIMIT 1`,
        [r.bot_id, userId, userId],
      );
      const botId = info[0]?.bot_id ?? r.bot_id;
      const env = info[0]?.env ?? null;
      const key = `${botId}\u0000${env ?? ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push({
        botId,
        botName: info[0]?.bot_name ?? null,
        env,
        accessType: "granted",
        ownerId: r.bot_owner_id,
      });
    }

    return result;
  }

  async listEligibleBotsForAnalyze(userId: string, workflowId: string): Promise<{ botId: string; botName: string | null; env: string | null; accessType: string; ownerId: string | null }[]> {
    const botRows = await this.db.query<{ bot_id: string; bot_owner_id: string }>(
      `SELECT bot_id, bot_owner_id FROM bot_workflow_permissions
       WHERE workflow_id = ? AND (can_view = 1 OR can_execute = 1 OR can_edit = 1) AND bot_id IS NOT NULL AND bot_id <> ''
         AND bot_owner_id = ?
         AND id IN (
           SELECT MAX(id) FROM bot_workflow_permissions
           WHERE workflow_id = ? AND bot_owner_id = ?
           GROUP BY bot_id
         )`,
      [workflowId, userId, workflowId, userId],
    );

    const result: { botId: string; botName: string | null; env: string | null; accessType: string; ownerId: string | null }[] = [];
    const seen = new Set<string>();

    for (const r of botRows) {
      const info = await this.db.query<{ bot_id: string; bot_name: string | null; env: string | null }>(
        `SELECT bot_id, bot_name, env FROM ac_bots
         WHERE bot_id = ? AND (owner_id = ? OR entity_id = ?) AND is_delete = 0
         ORDER BY id DESC LIMIT 1`,
        [r.bot_id, userId, userId],
      );
      const botId = info[0]?.bot_id ?? r.bot_id;
      const env = info[0]?.env ?? null;
      const key = `${botId}\0${env ?? ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push({
        botId,
        botName: info[0]?.bot_name ?? null,
        env,
        accessType: "granted",
        ownerId: r.bot_owner_id,
      });
    }

    return result;
  }



  async listSuggestionApplyTasks(suggestionIds: string[]): Promise<Array<{
    suggestionId: string;
    taskId: string;
    stepId: string;
    status: string;
    summary: string | null;
    botId: string | null;
    botName: string | null;
    botEnv: string | null;
    errorMessage: string | null;
    retryable: boolean;
    proposalDigest: string | null;
    proposal: Record<string, unknown> | null;
    applicationSpec: string | null;
    progress: {
      phase: string;
      message: string;
      elapsedMs: number;
      updatedAtMs: number;
      stalled: boolean;
      history: Array<{ phase: string; message: string; updatedAtMs: number }>;
    } | null;
    appliedAt: number | string | null;
    createdAt: number | string;
    updatedAt: number | string;
  }>> {
    if (suggestionIds.length === 0) return [];
    const clauses: string[] = [];
    const params: unknown[] = [];
    for (const id of suggestionIds) {
      clauses.push("(t.config_json LIKE ? OR t.config_json LIKE ?)");
      params.push(`%"suggestionId":"${id}"%`, `%"suggestionIds":%"${id}"%`);
    }
    const rows = await this.db.query<{
      task_id: string;
      step_id: string;
      status: string;
      summary: string | null;
      bot_id: string | null;
      bot_name: string | null;
      task_error_message: string | null;
      step_error_message: string | null;
      retryable: number | boolean | null;
      output_json: string | null;
      completed_at: number | string | null;
      gmt_create: number | string;
      gmt_modified: number | string;
      config_json: string;
    }>(
      `SELECT t.task_id, s.step_id, s.status, s.summary, t.bot_id,
       (SELECT b.bot_name FROM ac_bots b WHERE b.bot_id = t.bot_id AND b.is_delete = 0 ORDER BY b.id DESC LIMIT 1) AS bot_name,
       t.error_message AS task_error_message, s.error_message AS step_error_message, s.retryable, s.output_json, s.completed_at,
       t.gmt_create, t.gmt_modified, t.config_json
       FROM ce_tasks t
       JOIN ce_steps s ON s.task_id = t.task_id
       WHERE t.task_type = 'suggestion_apply'
         AND (${clauses.join(" OR ")})
       ORDER BY t.gmt_create DESC`,
      params,
    );
    const requested = new Set(suggestionIds);
    return rows.flatMap((r) => {
      const config = (() => {
        try { return JSON.parse(r.config_json); } catch { return null; }
      })() as Record<string, unknown> | null;
      const configIds = Array.isArray(config?.suggestionIds)
        ? config.suggestionIds.map(String)
        : [String(config?.suggestionId ?? "")];
      const revisions = Array.isArray(config?.suggestionRevisions)
        ? config.suggestionRevisions.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
        : [];
      const applicationInput = config?.applicationInput && typeof config.applicationInput === "object" && !Array.isArray(config.applicationInput)
        ? config.applicationInput as Record<string, unknown>
        : null;
      const applicationSpec = typeof applicationInput?.spec === "string" ? applicationInput.spec : null;
      const progress = (() => {
        try {
          const output = JSON.parse(r.output_json ?? "null") as { applicationProgress?: unknown } | null;
          const value = output?.applicationProgress;
          if (!value || typeof value !== "object" || Array.isArray(value)) return null;
          const item = value as Record<string, unknown>;
          if (typeof item.phase !== "string" || typeof item.message !== "string"
            || !Number.isFinite(Number(item.elapsedMs)) || !Number.isFinite(Number(item.updatedAtMs))) return null;
          const history = Array.isArray(item.history)
            ? item.history.flatMap((entry): Array<{ phase: string; message: string; updatedAtMs: number }> => {
              if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
              const historyItem = entry as Record<string, unknown>;
              if (typeof historyItem.phase !== "string" || typeof historyItem.message !== "string"
                || !Number.isFinite(Number(historyItem.updatedAtMs))) return [];
              return [{
                phase: historyItem.phase,
                message: historyItem.message,
                updatedAtMs: Number(historyItem.updatedAtMs),
              }];
            }).slice(-10)
            : [];
          return {
            phase: item.phase,
            message: item.message,
            elapsedMs: Math.max(
              Number(item.elapsedMs),
              Math.max(0, Date.now() - (() => {
                const numeric = Number(r.gmt_create);
                if (Number.isFinite(numeric) && numeric > 0) return numeric < 1e12 ? numeric * 1000 : numeric;
                const parsed = Date.parse(String(r.gmt_create));
                return Number.isFinite(parsed) ? parsed : Date.now();
              })()),
            ),
            updatedAtMs: Number(item.updatedAtMs),
            stalled: Date.now() - Number(item.updatedAtMs) > 90_000,
            history,
          };
        } catch { return null; }
      })();
      return configIds.filter((suggestionId) => requested.has(suggestionId)).map((suggestionId) => {
        const revision = revisions.find((item) => String(item.suggestionId ?? "") === suggestionId);
        const proposal = revision?.proposal && typeof revision.proposal === "object" && !Array.isArray(revision.proposal)
          ? revision.proposal as Record<string, unknown>
          : null;
        return {
        suggestionId,
        taskId: r.task_id,
        stepId: r.step_id,
        status: r.status,
        summary: r.summary,
        botId: r.bot_id ?? (typeof config?.botId === "string" ? config.botId : null),
        botName: r.bot_name,
        botEnv: typeof config?.botEnv === "string" ? config.botEnv : null,
        errorMessage: r.step_error_message ?? r.task_error_message,
        retryable: Boolean(r.retryable),
        proposalDigest: typeof revision?.proposalDigest === "string" ? revision.proposalDigest : null,
        proposal,
        applicationSpec,
        progress,
        appliedAt: r.completed_at,
        createdAt: r.gmt_create,
        updatedAt: r.gmt_modified,
      };
    });
    });
  }

  async listDiagnoses(options: {
    workflowId?: string | null;
    query?: string | null;
    limit?: number;
    offset?: number;
  } = {}): Promise<{ rows: EvolveRunDiagnosisRow[]; total: number }> {
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (options.workflowId?.trim()) {
      clauses.push("workflow_id = ?");
      params.push(options.workflowId.trim());
    }
    const query = options.query?.trim();
    if (query) {
      const like = `%${query}%`;
      clauses.push(`(
        diagnosis_id LIKE ? OR flow_id LIKE ? OR run_id LIKE ? OR node_id LIKE ? OR
        failure_signature LIKE ? OR failure_mode LIKE ? OR executor_type LIKE ? OR
        weak_node_id LIKE ? OR suggested_fix_kind LIKE ? OR lesson_id_hit LIKE ? OR error_text LIKE ?
      )`);
      params.push(like, like, like, like, like, like, like, like, like, like, like);
    }
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
    const totalRow = (await this.db.query<{ total: number }>(`SELECT COUNT(*) AS total FROM workflow_healing_diagnoses ${where}`, params))[0];
    const limit = Math.min(Math.max(Number(options.limit ?? 50), 1), 200);
    const offset = Math.max(Number(options.offset ?? 0), 0);
    const rows = await this.db.query<EvolveRunDiagnosisRow>(
      `SELECT * FROM workflow_healing_diagnoses ${where} ORDER BY gmt_create DESC, id DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );
    return { rows, total: Number(totalRow?.total ?? rows.length) };
  }

  async findDiagnosis(diagnosisId: string): Promise<EvolveRunDiagnosisRow | null> {
    return (await this.db.query<EvolveRunDiagnosisRow>("SELECT * FROM workflow_healing_diagnoses WHERE diagnosis_id = ?", [diagnosisId]))[0] ?? null;
  }

  async createDiagnosis(input: {
    diagnosisId: string;
    flowId: string;
    workflowId: string;
    runId?: string | null;
    nodeId?: string | null;
    failureSignature: string;
    failureMode: string;
    executorType?: string | null;
    weakNodeId?: string | null;
    suggestedFixKind?: string | null;
    lessonIdHit?: string | null;
    errorText?: string | null;
    createdBy?: string | null;
  }): Promise<EvolveRunDiagnosisRow> {
    const now = this.db.dialect.now();
    // Upsert by (flow_id, failure_signature, node_id, weak_node_id) so re-analysis of the
    // same run does not create duplicate diagnoses; the existing diagnosis_id is preserved
    // so that references in suggestions remain valid.
    const existing = (await this.db.query<EvolveRunDiagnosisRow>(
      `SELECT * FROM workflow_healing_diagnoses
       WHERE flow_id = ? AND failure_signature = ?
         AND (node_id = ? OR (node_id IS NULL AND ? IS NULL))
         AND (weak_node_id = ? OR (weak_node_id IS NULL AND ? IS NULL))
       LIMIT 1`,
      [
        input.flowId, input.failureSignature,
        input.nodeId ?? null, input.nodeId ?? null,
        input.weakNodeId ?? null, input.weakNodeId ?? null,
      ],
    ))[0];
    if (existing) {
      await this.db.exec(
        `UPDATE workflow_healing_diagnoses
         SET workflow_id = ?, run_id = ?, failure_mode = ?, executor_type = ?, suggested_fix_kind = ?,
             lesson_id_hit = ?, error_text = ?, created_by = ?, gmt_modified = ?
         WHERE id = ?`,
        [
          input.workflowId, input.runId ?? existing.run_id ?? null, input.failureMode,
          input.executorType ?? existing.executor_type, input.suggestedFixKind ?? existing.suggested_fix_kind,
          input.lessonIdHit ?? existing.lesson_id_hit, input.errorText ?? existing.error_text,
          input.createdBy ?? existing.created_by, now, existing.id,
        ],
      );
      const row = await this.findDiagnosis(existing.diagnosis_id);
      if (!row) throw new Error("更新 diagnosis 失败");
      return row;
    }
    await this.db.exec(
      `INSERT INTO workflow_healing_diagnoses
       (diagnosis_id, flow_id, workflow_id, run_id, node_id, failure_signature, failure_mode, executor_type, weak_node_id, suggested_fix_kind, lesson_id_hit, error_text, created_by, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.diagnosisId, input.flowId, input.workflowId, input.runId ?? null, input.nodeId ?? null,
        input.failureSignature, input.failureMode, input.executorType ?? null, input.weakNodeId ?? null,
        input.suggestedFixKind ?? null, input.lessonIdHit ?? null, input.errorText ?? null,
        input.createdBy ?? null, now, now,
      ],
    );
    const row = await this.findDiagnosis(input.diagnosisId);
    if (!row) throw new Error("创建 diagnosis 失败");
    await this.recordSuggestionRecurrence(input.workflowId, input.failureSignature);
    return row;
  }


  async findSuggestionById(suggestionId: string | number): Promise<EvolveSuggestionRow | null> {
    return (await this.db.query<EvolveSuggestionRow>("SELECT * FROM workflow_healing_suggestions WHERE id = ?", [String(suggestionId)]))[0] ?? null;
  }

  async findSuggestionBySignature(workflowId: string, signature: string): Promise<EvolveSuggestionRow | null> {
    return (await this.db.query<EvolveSuggestionRow>(
      "SELECT * FROM workflow_healing_suggestions WHERE workflow_id = ? AND failure_signature = ? ORDER BY gmt_create DESC LIMIT 1",
      [workflowId, signature],
    ))[0] ?? null;
  }

  async createSuggestion(input: CreateSuggestionInput): Promise<EvolveSuggestionRow> {
    const existing = await this.findSuggestionBySignature(input.workflowId, input.failureSignature);
    if (existing) {
      return this.updateSuggestion(existing.id, input);
    }
    const now = this.db.dialect.now();
    const sourceIdsJson = JSON.stringify([...(input.sourceDiagnosisIds ?? [])]);
    const impactIdsJson = JSON.stringify([...(input.impactRunIds ?? [])]);
    const insertResult = await this.db.exec(
      `INSERT INTO workflow_healing_suggestions
       (workflow_id, node_id, weak_node_id, failure_signature, failure_mode, fix_kind, fix_spec, source_diagnosis_ids, impact_run_ids, status, action_log, created_by, updated_by, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.workflowId, input.nodeId ?? null, input.weakNodeId ?? null,
        input.failureSignature, input.failureMode ?? null, input.fixKind ?? null, input.fixSpec ?? null,
        sourceIdsJson, impactIdsJson, input.status ?? "pending", "[]", input.createdBy ?? null,
        input.updatedBy ?? input.createdBy ?? null, now, now,
      ],
    );
    const row = await this.findSuggestionById(insertResult.insertId ?? -1);
    if (!row) throw new Error("创建 suggestion 失败");
    return row;
  }

  private async updateSuggestion(suggestionId: number, input: CreateSuggestionInput): Promise<EvolveSuggestionRow> {
    const existing = await this.findSuggestionById(suggestionId);
    if (!existing) throw new Error("更新 suggestion 失败：记录不存在");
    const mergedSourceIds = [...new Set([...this.parseJsonStringArray(existing.source_diagnosis_ids), ...(input.sourceDiagnosisIds ?? [])])];
    const mergedImpactIds = [...new Set([...this.parseJsonStringArray(existing.impact_run_ids), ...(input.impactRunIds ?? [])])];
    const newStatus = existing.status === "pending" ? (input.status ?? existing.status) : existing.status;
    const now = this.db.dialect.now();
    await this.db.exec(
      `UPDATE workflow_healing_suggestions
       SET node_id = ?, weak_node_id = ?, failure_mode = ?, fix_kind = ?, fix_spec = ?,
           source_diagnosis_ids = ?, impact_run_ids = ?, status = ?, updated_by = ?, gmt_modified = ?
       WHERE id = ?`,
      [
        input.nodeId ?? existing.node_id, input.weakNodeId ?? existing.weak_node_id,
        input.failureMode ?? existing.failure_mode, input.fixKind ?? existing.fix_kind,
        input.fixSpec ?? existing.fix_spec, JSON.stringify(mergedSourceIds), JSON.stringify(mergedImpactIds),
        newStatus, input.updatedBy ?? input.createdBy ?? existing.updated_by, now, suggestionId,
      ],
    );
    const row = (await this.findSuggestionById(suggestionId))!;
    return row;
  }

  async listSuggestions(options: {
    workflowId?: string | null;
    status?: string | null;
    limit?: number;
    offset?: number;
  } = {}): Promise<{ rows: EvolveSuggestionRow[]; total: number }> {
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (options.workflowId?.trim()) {
      clauses.push("workflow_id = ?");
      params.push(options.workflowId.trim());
    }
    if (options.status?.trim()) {
      clauses.push("status = ?");
      params.push(options.status.trim());
    }
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
    const totalRow = (await this.db.query<{ total: number }>(`SELECT COUNT(*) AS total FROM workflow_healing_suggestions ${where}`, params))[0];
    const limit = Math.min(Math.max(Number(options.limit ?? 50), 1), 200);
    const offset = Math.max(Number(options.offset ?? 0), 0);
    const rows = await this.db.query<EvolveSuggestionRow>(
      `SELECT * FROM workflow_healing_suggestions ${where} ORDER BY gmt_create DESC, id DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );
    return { rows, total: Number(totalRow?.total ?? rows.length) };
  }

  async updateSuggestionStatus(suggestionId: string | number, status: string, actionLogEntry: Record<string, unknown>): Promise<EvolveSuggestionRow | null> {
    const current = await this.findSuggestionById(suggestionId);
    if (!current) return null;
    const log = this.parseActionLog(current.action_log);
    log.push(actionLogEntry);
    const updatedBy = typeof actionLogEntry.actor === "string" ? actionLogEntry.actor : current.updated_by;
    await this.db.exec(
      `UPDATE workflow_healing_suggestions
       SET status = ?, action_log = ?, updated_by = ?, gmt_modified = ?
       WHERE id = ?`,
      [status, JSON.stringify(log), updatedBy, this.db.dialect.now(), suggestionId],
    );
    return this.findSuggestionById(suggestionId);
  }

  async markSuggestionAppliedUnverified(
    suggestionId: string | number,
    input: { actor: string; note?: string | null },
  ): Promise<EvolveSuggestionRow | null> {
    const current = await this.findSuggestionById(suggestionId);
    if (!current) return null;
    if (current.status === "applied_unverified") return current;
    if (!new Set(["adopted", "applying"]).has(current.status)) {
      throw new Error(`当前建议状态为 ${current.status}，只能记录已采纳或应用中的建议`);
    }
    const appliedAt = Math.floor(Date.now() / 1000);
    const log = this.parseActionLog(current.action_log);
    log.push({
      action: "applied_unverified",
      actor: input.actor,
      note: input.note ?? "Bot 已完成工作流修改，等待后续自然流量或人工验证",
      timestamp: new Date(appliedAt * 1000).toISOString(),
    });
    await this.db.exec(
      `UPDATE workflow_healing_suggestions
       SET status = 'applied_unverified', applied_at = ?, verification_status = 'observing',
           verification_checked_at = NULL, recurrence_count = 0, last_recurrence_at = NULL,
           action_log = ?, updated_by = ?, gmt_modified = ?
       WHERE id = ?`,
      [appliedAt, JSON.stringify(log), input.actor, this.db.dialect.now(), suggestionId],
    );
    return this.findSuggestionById(suggestionId);
  }

  async markSuggestionVerification(
    suggestionId: string | number,
    outcome: "verified" | "ineffective",
    input: { actor: string; note?: string | null },
  ): Promise<EvolveSuggestionRow | null> {
    const current = await this.findSuggestionById(suggestionId);
    if (!current) return null;
    if (current.status !== "applied_unverified") {
      throw new Error(`当前建议状态为 ${current.status}，只能验证已应用待验证的建议`);
    }
    const checkedAt = Math.floor(Date.now() / 1000);
    const log = this.parseActionLog(current.action_log);
    log.push({
      action: outcome,
      actor: input.actor,
      note: input.note ?? (outcome === "verified" ? "人工确认修复有效" : "人工确认建议未达到预期"),
      timestamp: new Date(checkedAt * 1000).toISOString(),
    });
    await this.db.exec(
      `UPDATE workflow_healing_suggestions
       SET status = ?, verification_status = ?, verification_checked_at = ?,
           action_log = ?, updated_by = ?, gmt_modified = ?
       WHERE id = ?`,
      [outcome, outcome, checkedAt, JSON.stringify(log), input.actor, this.db.dialect.now(), suggestionId],
    );
    return this.findSuggestionById(suggestionId);
  }

  private async recordSuggestionRecurrence(workflowId: string, failureSignature: string): Promise<void> {
    const observedAt = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE workflow_healing_suggestions
       SET verification_status = 'recurrence_detected', verification_checked_at = ?,
           recurrence_count = recurrence_count + 1, last_recurrence_at = ?, gmt_modified = ?
       WHERE workflow_id = ? AND failure_signature = ?
         AND status = 'applied_unverified' AND applied_at IS NOT NULL AND applied_at <= ?`,
      [observedAt, observedAt, this.db.dialect.now(), workflowId, failureSignature, observedAt],
    );
  }

  async updateDiagnosesSuggestionStatus(workflowId: string, signature: string, suggestionId: string, status: string): Promise<void> {
    const hasSuggestionId = await this.diagnosisHasColumn("suggestion_id");
    const hasSuggestionStatus = await this.diagnosisHasColumn("suggestion_status");
    if (!hasSuggestionId && !hasSuggestionStatus) return;
    const sets: string[] = [];
    const params: unknown[] = [];
    if (hasSuggestionId) { sets.push("suggestion_id = ?"); params.push(suggestionId); }
    if (hasSuggestionStatus) { sets.push("suggestion_status = ?"); params.push(status); }
    sets.push("gmt_modified = ?");
    params.push(this.db.dialect.now(), workflowId, signature);
    await this.db.exec(
      `UPDATE workflow_healing_diagnoses SET ${sets.join(", ")} WHERE workflow_id = ? AND failure_signature = ?`,
      params,
    );
  }

  async backfillDiagnosisLessonHit(workflowId: string, signature: string, lessonId: string): Promise<void> {
    const hasSuggestionStatus = await this.diagnosisHasColumn("suggestion_status");
    const sets = ["lesson_id_hit = ?"];
    const params: unknown[] = [lessonId];
    if (hasSuggestionStatus) { sets.push("suggestion_status = 'resolved'"); }
    sets.push("gmt_modified = ?");
    params.push(this.db.dialect.now(), workflowId, signature);
    await this.db.exec(
      `UPDATE workflow_healing_diagnoses SET ${sets.join(", ")} WHERE workflow_id = ? AND failure_signature = ?`,
      params,
    );
  }

  private diagnosisColumnCache: Set<string> | null = null;

  private async loadDiagnosisColumns(): Promise<Set<string>> {
    if (this.diagnosisColumnCache) return this.diagnosisColumnCache;
    let columns: Set<string>;
    if (this.db.dbType === "sqlite") {
      const rows = await this.db.query<{ name: string }>("PRAGMA table_info(workflow_healing_diagnoses)");
      columns = new Set(rows.map((row) => row.name));
    } else {
      const rows = await this.db.query<{ column_name: string }>(
        `SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = ?`,
        ["workflow_healing_diagnoses"],
      );
      columns = new Set(rows.map((row) => row.column_name));
    }
    this.diagnosisColumnCache = columns;
    return columns;
  }

  private async diagnosisHasColumn(name: string): Promise<boolean> {
    return (await this.loadDiagnosisColumns()).has(name);
  }


  private parseJsonStringArray(value: string | null | undefined): string[] {
    if (!value) return [];
    try {
      const parsed = JSON.parse(value) as unknown;
      return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
    } catch {
      return [];
    }
  }

  private parseActionLog(value: string | null | undefined): unknown[] {
    if (!value) return [];
    try {
      const parsed = JSON.parse(value) as unknown;
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  async listSuggestionActions(options: {
    workflowId?: string | null;
    signature?: string | null;
    limit?: number;
    offset?: number;
  } = {}): Promise<{ rows: EvolveSuggestionActionRow[]; total: number }> {
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (options.workflowId?.trim()) {
      clauses.push("workflow_id = ?");
      params.push(options.workflowId.trim());
    }
    if (options.signature?.trim()) {
      clauses.push("signature = ?");
      params.push(options.signature.trim());
    }
    const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";
    const totalRow = (await this.db.query<{ total: number }>(`SELECT COUNT(*) AS total FROM workflow_healing_suggestion_actions ${where}`, params))[0];
    const limit = Math.min(Math.max(Number(options.limit ?? 50), 1), 200);
    const offset = Math.max(Number(options.offset ?? 0), 0);
    const rows = await this.db.query<EvolveSuggestionActionRow>(
      `SELECT * FROM workflow_healing_suggestion_actions ${where} ORDER BY gmt_create DESC, id DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );
    return { rows, total: Number(totalRow?.total ?? rows.length) };
  }

  async recordSuggestionAction(input: SuggestionActionInput): Promise<EvolveSuggestionActionRow> {
    const now = this.db.dialect.now();
    const isMysql = this.db.dbType === "mysql" || this.db.dbType === "zdas";
    const onConflict = isMysql
      ? "ON DUPLICATE KEY UPDATE action = VALUES(action), fix_kind = VALUES(fix_kind), note = VALUES(note), created_by = VALUES(created_by), gmt_modified = VALUES(gmt_modified)"
      : "ON CONFLICT(workflow_id, signature) DO UPDATE SET action = EXCLUDED.action, fix_kind = EXCLUDED.fix_kind, note = EXCLUDED.note, created_by = EXCLUDED.created_by, gmt_modified = EXCLUDED.gmt_modified";
    await this.db.exec(
      `INSERT INTO workflow_healing_suggestion_actions
       (signature, workflow_id, node_id, action, fix_kind, note, created_by, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
       ${onConflict}`,
      [input.signature, input.workflowId, input.nodeId ?? null, input.action, input.fixKind ?? null, input.note ?? null, input.createdBy ?? null, now, now],
    );
    const row = (await this.db.query<EvolveSuggestionActionRow>(
      "SELECT * FROM workflow_healing_suggestion_actions WHERE workflow_id = ? AND signature = ?",
      [input.workflowId, input.signature],
    ))[0];
    if (!row) throw new Error("记录 suggestion action 失败");
    const actionableStatus = input.action === "adopt" ? "adopted"
      : input.action === "reject" ? "rejected"
      : input.action === "bench" ? "benched"
      : input.action;
    if (["adopt", "reject", "bench", "adopted", "rejected", "benched"].includes(input.action)) {
      const suggestion = await this.findSuggestionBySignature(input.workflowId, input.signature);
      if (suggestion) {
        await this.updateDiagnosesSuggestionStatus(input.workflowId, input.signature, String(suggestion.id), actionableStatus);
        await this.updateSuggestionStatus(suggestion.id, actionableStatus, {
          action: actionableStatus,
          actor: input.createdBy ?? "system",
          note: input.note,
          timestamp: new Date().toISOString(),
        });
      }
    }
    return row;
  }

  async getWorkflowIdByFlowId(flowId: string): Promise<string | null> {
    const row = (await this.db.query<{ workflow_id: string | null }>(
      "SELECT workflow_id FROM flow_runs WHERE flow_id = ? LIMIT 1",
      [flowId],
    ))[0];
    return row?.workflow_id ?? null;
  }

  async findRunningRunAnalysisTask(flowId: string): Promise<{ task_id: string; step_id: string; status: string; gmt_create: number | string } | null> {
    const row = (await this.db.query<{ task_id: string; step_id: string; status: string; gmt_create: number | string }>(
      `SELECT task_id, step_id, status, gmt_create FROM ce_steps
       WHERE step_type = ? AND command LIKE ?
         AND status NOT IN ('completed', 'failed', 'canceled')
       ORDER BY gmt_create DESC LIMIT 1`,
      ["run_analysis", `[run-analysis] ${flowId}%`],
    ))[0];
    return row ?? null;
  }

  async findStaleRunAnalysisSteps(staleMs: number): Promise<{ step_id: string; task_id: string; flow_id: string; gmt_create: number | string }[]> {
    const threshold = this.db.dialect.epochToDb(Math.floor((Date.now() - staleMs) / 1000));
    return this.db.query<{ step_id: string; task_id: string; flow_id: string; gmt_create: number | string }>(
      `SELECT s.step_id, s.task_id, TRIM(SUBSTR(s.command, 15)) AS flow_id, s.gmt_create
       FROM ce_steps s
       WHERE s.step_type = 'run_analysis'
         AND s.status IN ('created', 'dispatching', 'dispatched', 'running', 'analyzing')
         AND s.gmt_create < ?`,
      [threshold],
    );
  }

  async tryTimeoutRunAnalysisStep(
    stepId: string,
    flowId: string,
    errorCode: string,
    errorMessage: string,
    completedAtMs = Date.now(),
  ): Promise<boolean> {
    const now = this.db.dialect.now();
    return this.db.transaction(async (tx) => {
      const step = (await tx.query<{ task_id: string }>(
        "SELECT task_id FROM ce_steps WHERE step_id = ? LIMIT 1",
        [stepId],
      ))[0];
      if (!step) return false;
      const updated = await tx.exec(
        `UPDATE ce_steps
         SET status = 'failed', error_code = ?, error_message = ?, retryable = 1,
             completed_at = ?, gmt_modified = ?
         WHERE step_id = ? AND step_type = 'run_analysis'
           AND status IN ('created', 'dispatching', 'dispatched', 'running', 'analyzing')`,
        [errorCode, errorMessage, now, now, stepId],
      );
      if ((updated.affectedRows ?? 0) !== 1) return false;
      await tx.exec(
        "UPDATE ce_tasks SET status = 'failed', error_message = ?, gmt_modified = ? WHERE task_id = ?",
        [errorMessage, now, step.task_id],
      );
      await tx.exec(
        `UPDATE workflow_evolution_analysis_runs
         SET status = 'failed', error_code = ?, completed_at_ms = ?,
             state_version = state_version + 1, gmt_modified = ?
         WHERE (step_id = ? OR task_id = ?)
           AND status IN ('queued', 'collecting', 'analyzing')`,
        [errorCode, completedAtMs, now, stepId, step.task_id],
      );
      if (flowId) {
        await tx.exec(
          "UPDATE flow_runs SET evolution_analysis_status = 'failed', evolution_analyzed_at = ? WHERE flow_id = ?",
          [now, flowId],
        );
      }
      return true;
    });
  }

  async clearFlowDiagnoses(flowId: string): Promise<number> {
    const result = await this.db.exec(
      "DELETE FROM workflow_healing_diagnoses WHERE flow_id = ?",
      [flowId],
    );
    return result.affectedRows ?? 0;
  }

  async getFlowRun(flowId: string): Promise<{ workflow_id: string; origin_bot_id: string | null; plugin_version: string | null; engine: string | null } | null> {
    const row = (await this.db.query<{ workflow_id: string; origin_bot_id: string | null; plugin_version: string | null; engine: string | null }>(
      "SELECT workflow_id, origin_bot_id, plugin_version, engine FROM flow_runs WHERE flow_id = ? LIMIT 1",
      [flowId],
    ))[0];
    return row ?? null;
  }

  async countDiagnosesByFlow(flowId: string): Promise<number> {
    const row = (await this.db.query<{ total: number }>(
      "SELECT COUNT(*) AS total FROM workflow_healing_diagnoses WHERE flow_id = ?",
      [flowId],
    ))[0];
    return Number(row?.total ?? 0);
  }

  async getFlowAnalysisStatus(flowId: string): Promise<string | null> {
    const row = (await this.db.query<{ evolution_analysis_status: string | null }>(
      "SELECT evolution_analysis_status FROM flow_runs WHERE flow_id = ?",
      [flowId],
    ))[0];
    return row?.evolution_analysis_status ?? null;
  }

  async startFlowAnalysis(flowId: string): Promise<boolean> {
    const result = await this.db.exec(
      "UPDATE flow_runs SET evolution_analysis_status = 'analyzing', evolution_analyzed_at = ? WHERE flow_id = ? AND (evolution_analysis_status IS NULL OR evolution_analysis_status IN ('failed', 'completed'))",
      [this.db.dialect.now(), flowId],
    );
    return result.affectedRows === 1;
  }

  async completeFlowAnalysis(flowId: string): Promise<void> {
    await this.db.exec(
      "UPDATE flow_runs SET evolution_analysis_status = 'completed', evolution_analyzed_at = ? WHERE flow_id = ?",
      [this.db.dialect.now(), flowId],
    );
  }

  async failFlowAnalysis(flowId: string): Promise<void> {
    await this.db.exec(
      "UPDATE flow_runs SET evolution_analysis_status = 'failed' WHERE flow_id = ?",
      [flowId],
    );
  }

  async resetFlowAnalysis(flowId: string): Promise<number> {
    const now = this.db.dialect.now();
    const steps = (await this.db.query<{ step_id: string }>(
      `SELECT step_id FROM ce_steps
       WHERE step_type = 'run_analysis' AND command LIKE ? AND status IN ('created', 'dispatching', 'dispatched', 'running', 'analyzing')`,
      [`[run-analysis] ${flowId}%`],
    ));
    let canceled = 0;
    for (const row of steps) {
      await this.updateStepStatus(row.step_id, {
        status: "canceled",
        errorCode: "RUN_ANALYSIS_RESET",
        errorMessage: "用户手动重置分析状态",
      });
      canceled++;
    }
    await this.db.exec(
      "UPDATE flow_runs SET evolution_analysis_status = 'failed', evolution_analyzed_at = ? WHERE flow_id = ?",
      [now, flowId],
    );
    return canceled;
  }

  async listUnanalyzedFailedFlows(workflowId: string, since: number): Promise<Array<{
    flow_id: string;
    workflow_id: string;
    status: string;
    failed_count: number;
    started_at: number | string;
    completed_at: number | string | null;
  }>> {
    return this.db.query(
      `SELECT flow_id, workflow_id, status, failed_count, started_at, completed_at
       FROM flow_runs
       WHERE workflow_id = ? AND status = 'failed' AND failed_count > 0
         AND gmt_create >= ?
         AND evolution_analysis_status IS NULL
       ORDER BY completed_at DESC`,
      [workflowId, since],
    );
  }

  async listAnalyzedFlows(workflowId: string): Promise<Array<{
    flow_id: string;
    evolution_analysis_status: string | null;
    evolution_analyzed_at: number | string | null;
  }>> {
    return this.db.query(
      `SELECT flow_id, evolution_analysis_status, evolution_analyzed_at
       FROM flow_runs
       WHERE workflow_id = ? AND evolution_analysis_status IS NOT NULL
       ORDER BY evolution_analyzed_at DESC`,
      [workflowId],
    );
  }

  async listWeakLinks(options: {
    workflowId: string;
    limit?: number;
    offset?: number;
  }): Promise<{ rows: Array<{
    failure_signature: string;
    node_id: string;
    executor_type: string | null;
    failure_mode: string;
    occurrence_count: number;
    evidence_flow_ids: string | null;
  }>; total: number }> {
    const params: unknown[] = [options.workflowId];
    const totalRow = (await this.db.query<{ total: number }>(
      `SELECT COUNT(*) AS total FROM (
         SELECT failure_signature FROM workflow_healing_diagnoses WHERE workflow_id = ? GROUP BY failure_signature
       ) t`,
      params,
    ))[0];
    const limit = Math.min(Math.max(Number(options.limit ?? 50), 1), 200);
    const offset = Math.max(Number(options.offset ?? 0), 0);
    const rows = await this.db.query<{
      failure_signature: string;
      node_id: string;
      executor_type: string | null;
      failure_mode: string;
      occurrence_count: number;
      evidence_flow_ids: string | null;
    }>(
      `SELECT failure_signature, node_id, executor_type, failure_mode,
              COUNT(*) AS occurrence_count,
              GROUP_CONCAT(DISTINCT flow_id) AS evidence_flow_ids
       FROM workflow_healing_diagnoses
       WHERE workflow_id = ?
       GROUP BY failure_signature, node_id, executor_type, failure_mode
       ORDER BY occurrence_count DESC, MAX(gmt_create) DESC
       LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );
    return { rows, total: Number(totalRow?.total ?? rows.length) };
  }

  private taskUsesPack(task: EvolveTaskRow, pack: EvolvePackRow): boolean {
    let config: Record<string, unknown>;
    try { config = JSON.parse(task.config_json) as Record<string, unknown>; } catch { return false; }
    if (config.packId === pack.pack_id) return true;
    return config.sourceTaskId === pack.source_task_id
      && config.sourceKind === pack.source_kind
      && Number(config.sourceRound ?? 0) === pack.source_round;
  }

  async createStep(input: {
    stepId: string; taskId: string; stepType: string; stepNo: number;
    roundNo?: number | null; command: string;
  }): Promise<EvolveStepRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO ce_steps
       (step_id, task_id, step_type, step_no, round_no, command, status, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, 'created', ?, ?)`,
      [input.stepId, input.taskId, input.stepType, input.stepNo, input.roundNo ?? null,
        input.command, now, now],
    );
    const row = await this.findStep(input.stepId);
    if (!row) throw new Error("创建 Step 失败");
    return row;
  }

  async deleteTask(taskId: string): Promise<void> {
    await this.db.transaction(async (tx) => {
      await tx.exec("DELETE FROM ce_steps WHERE task_id = ?", [taskId]);
      await tx.exec("DELETE FROM ce_task_sources WHERE task_id = ?", [taskId]);
      await tx.exec("DELETE FROM ce_tasks WHERE task_id = ?", [taskId]);
    });
  }

  async findTask(taskId: string): Promise<EvolveTaskRow | null> {
    return (await this.db.query<EvolveTaskRow>("SELECT * FROM ce_tasks WHERE task_id = ?", [taskId]))[0] ?? null;
  }
  async listTasks(limit = 100): Promise<EvolveTaskRow[]> {
    return this.db.query<EvolveTaskRow>("SELECT * FROM ce_tasks ORDER BY gmt_create DESC LIMIT ?", [limit]);
  }
  async listTasksPage(input: EvolveTaskPageQuery): Promise<{ rows: EvolveTaskRow[]; total: number }> {
    const clauses: string[] = [];
    const params: unknown[] = [];
    if (input.createdBy) { clauses.push("created_by = ?"); params.push(input.createdBy); }
    if (input.ownerUserId) { clauses.push("user_id = ?"); params.push(input.ownerUserId); }
    if (input.taskTypes?.length) {
      clauses.push(`task_type IN (${input.taskTypes.map(() => "?").join(",")})`);
      params.push(...input.taskTypes);
    }
    if (input.excludedTaskTypes?.length) {
      clauses.push(`task_type NOT IN (${input.excludedTaskTypes.map(() => "?").join(",")})`);
      params.push(...input.excludedTaskTypes);
    }
    if (input.statuses?.length) {
      clauses.push(`status IN (${input.statuses.map(() => "?").join(",")})`);
      params.push(...input.statuses);
    }
    const keyword = input.query?.trim();
    if (keyword) {
      clauses.push("(task_name LIKE ? OR remark LIKE ? OR bot_id LIKE ? OR user_id LIKE ? OR task_id LIKE ?)");
      const pattern = `%${keyword}%`;
      params.push(pattern, pattern, pattern, pattern, pattern);
    }
    const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
    const countRows = await this.db.query<{ total: number | string }>(`SELECT COUNT(*) AS total FROM ce_tasks ${where}`, params);
    const offset = (input.page - 1) * input.pageSize;
    const rows = await this.db.query<EvolveTaskRow>(
      `SELECT * FROM ce_tasks ${where} ORDER BY gmt_create DESC LIMIT ? OFFSET ?`,
      [...params, input.pageSize, offset],
    );

    const botIds = [...new Set(rows.map((row) => row.bot_id).filter(Boolean))];
    if (botIds.length) {
      const bots = await this.db.query<{ id: number; bot_id: string; owner_id: string | null; entity_id: string | null; bot_name: string | null }>(
        `SELECT id, bot_id, owner_id, entity_id, bot_name FROM ac_bots
         WHERE bot_id IN (${botIds.map(() => "?").join(",")}) AND is_delete = 0 ORDER BY id DESC`,
        botIds,
      ).catch(() => []);
      const names = new Map<string, string>();
      for (const bot of bots) {
        if (!bot.bot_name) continue;
        for (const owner of [bot.owner_id, bot.entity_id]) {
          if (owner && !names.has(`${owner}:${bot.bot_id}`)) names.set(`${owner}:${bot.bot_id}`, bot.bot_name);
        }
      }
      for (const row of rows) row.bot_name = names.get(`${row.user_id}:${row.bot_id}`) ?? null;
    }
    return { rows, total: Number(countRows[0]?.total ?? 0) };
  }
  async listTasksByUserAndTypes(userId: string, taskTypes: string[], limit = 100): Promise<EvolveTaskRow[]> {
    if (!taskTypes.length) return [];
    return this.db.query<EvolveTaskRow>(
      `SELECT * FROM ce_tasks WHERE created_by = ? AND task_type IN (${taskTypes.map(() => "?").join(",")})
       ORDER BY gmt_create DESC LIMIT ?`,
      [userId, ...taskTypes, limit],
    );
  }
  async listActiveTasksByTypes(taskTypes: string[], limit = 100): Promise<EvolveTaskRow[]> {
    if (!taskTypes.length) return [];
    return this.db.query<EvolveTaskRow>(
      `SELECT * FROM ce_tasks WHERE task_type IN (${taskTypes.map(() => "?").join(",")})
       AND status IN ('pending', 'running') ORDER BY gmt_modified LIMIT ?`,
      [...taskTypes, limit],
    );
  }
  async findActiveRestoreTask(userId: string, botId: string): Promise<EvolveTaskRow | null> {
    return (await this.db.query<EvolveTaskRow>(
      `SELECT * FROM ce_tasks
       WHERE user_id = ? AND bot_id = ? AND task_type = 'pack_restore'
         AND status IN ('pending', 'running')
       ORDER BY gmt_create DESC LIMIT 1`,
      [userId, botId],
    ))[0] ?? null;
  }
  async listActiveBotEvolveTasks(userId: string, botId: string): Promise<EvolveTaskRow[]> {
    return this.db.query<EvolveTaskRow>(
      `SELECT * FROM ce_tasks
       WHERE user_id = ? AND bot_id = ? AND task_type <> 'runtime_cleanup'
         AND status IN ('pending', 'running')
       ORDER BY gmt_create DESC`,
      [userId, botId],
    );
  }
  async updateTaskConfig(taskId: string, config: Record<string, unknown>): Promise<void> {
    await this.db.exec(
      "UPDATE ce_tasks SET config_json = ?, gmt_modified = ? WHERE task_id = ?",
      [JSON.stringify(config), this.db.dialect.now(), taskId],
    );
  }
  async updateTaskState(input: {
    taskId: string;
    status: string;
    config?: Record<string, unknown>;
    errorMessage?: string | null;
  }): Promise<void> {
    const now = nowForDb(this.db.dbType);
    if (input.config) {
      await this.db.exec(
        "UPDATE ce_tasks SET status = ?, config_json = ?, error_message = ?, gmt_modified = ? WHERE task_id = ?",
        [input.status, JSON.stringify(input.config), input.errorMessage ?? null, now, input.taskId],
      );
      return;
    }
    await this.db.exec(
      "UPDATE ce_tasks SET status = ?, error_message = ?, gmt_modified = ? WHERE task_id = ?",
      [input.status, input.errorMessage ?? null, now, input.taskId],
    );
  }
  async prepareTaskRetry(taskId: string, config: Record<string, unknown>): Promise<void> {
    await this.db.exec(
      "UPDATE ce_tasks SET status = 'pending', config_json = ?, error_message = NULL, gmt_modified = ? WHERE task_id = ?",
      [JSON.stringify(config), this.db.dialect.now(), taskId],
    );
  }
  async resolveBotDispatchMode(userId: string, botId: string, env?: string): Promise<"message" | "run"> {
    const runtime = await this.resolveEvolveBotRuntime(userId, botId, env);
    return runtime?.botType?.toLowerCase() === "service" ? "run" : "message";
  }

  async listEvolveBots(userId: string): Promise<EvolveBotOption[]> {
    const rows = await this.db.query<{ id: number; bot_id: string; bot_name: string | null; env: string | null; active_engine: string | null; bot_type: string | null }>(
      `SELECT id, bot_id, bot_name, env, active_engine, bot_type FROM ac_bots
       WHERE (owner_id = ? OR entity_id = ?) AND is_delete = 0
         AND bot_id IS NOT NULL AND bot_id <> '' ORDER BY id DESC`,
      [userId, userId],
    );
    const seen = new Set<string>();
    return rows.filter((row) => { const key = `${row.bot_id}\u0000${row.env ?? ""}`; if (seen.has(key)) return false; seen.add(key); return true; })
      .map((row) => ({ botId: row.bot_id, botName: row.bot_name, env: row.env,
        activeEngine: row.active_engine, botType: row.bot_type }));
  }

  async listAccessibleEvolveBots(userId: string): Promise<EvolveBotOption[]> {
    const owned = (await this.listEvolveBots(userId)).map((bot) => ({
      ...bot, ownerId: userId, accessType: "owner" as const,
    }));
    const collaborated = await this.db.query<{
      bot_id: string; bot_name: string | null; env: string | null;
      active_engine: string | null; bot_type: string | null; owner_id: string | null;
    }>(
      `SELECT b.bot_id, b.bot_name, b.env, b.active_engine, b.bot_type, c.owner_id
       FROM ac_bot_collaborator c
       JOIN ac_bots b ON b.bot_id = c.bot_id
         AND (b.owner_id = c.owner_id OR b.entity_id = c.owner_id)
         AND b.is_delete = 0
       WHERE c.user_id = ? AND c.bot_id IS NOT NULL AND c.bot_id <> ''
       ORDER BY c.id DESC, b.id DESC`,
      [userId],
    ).catch(() => []);
    const seen = new Set(owned.map((bot) => `${bot.ownerId ?? userId}\u0000${bot.botId}\u0000${bot.env ?? ""}`));
    return [...owned, ...collaborated.filter((row) => {
      const key = `${row.owner_id ?? ""}\u0000${row.bot_id}\u0000${row.env ?? ""}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).map((row) => ({
      botId: row.bot_id, botName: row.bot_name, env: row.env,
      activeEngine: row.active_engine, botType: row.bot_type,
      ownerId: row.owner_id, accessType: "collaborator" as const,
    }))];
  }

  async resolveAccessibleEvolveBotRuntime(
    userId: string,
    botId: string,
    env?: string,
  ): Promise<AccessibleEvolveBotRuntime | null> {
    const owned = await this.resolveOwnedEvolveBotRuntime(userId, botId, env);
    if (owned) return { runtime: owned, ownerId: userId, accessType: "owner" };
    const collaborator = (await this.db.query<{ owner_id: string | null }>(
      `SELECT c.owner_id
       FROM ac_bot_collaborator c
       JOIN ac_bots b ON b.bot_id = c.bot_id
         AND (b.owner_id = c.owner_id OR b.entity_id = c.owner_id)
         AND b.is_delete = 0
       WHERE c.user_id = ? AND c.bot_id = ? AND c.owner_id IS NOT NULL AND c.owner_id <> ''
         AND (? = '' OR b.env = ?)
       ORDER BY c.id DESC, b.id DESC LIMIT 1`,
      [userId, botId, env ?? "", env ?? ""],
    ).catch(() => []))[0];
    if (!collaborator?.owner_id) return null;
    const runtime = await this.resolveOwnedEvolveBotRuntime(collaborator.owner_id, botId, env);
    return runtime ? {
      runtime, ownerId: collaborator.owner_id, accessType: "collaborator",
    } : null;
  }

  async resolveEvolveBotRuntime(userId: string, botId: string, env?: string): Promise<EvolveBotRuntime | null> {
    const accessible = await this.resolveAccessibleEvolveBotRuntime(userId, botId, env);
    return accessible ? {
      ...accessible.runtime,
      ownerId: accessible.ownerId,
      accessType: accessible.accessType,
    } : null;
  }

  /** Resolve a Bot by its real owner. Admin authorization stays in the caller. */
  async resolveEvolveBotRuntimeForOwner(
    ownerId: string,
    botId: string,
    env?: string,
  ): Promise<EvolveBotRuntime | null> {
    const runtime = await this.resolveOwnedEvolveBotRuntime(ownerId, botId, env);
    return runtime ? { ...runtime, ownerId, accessType: "owner" } : null;
  }

  private async resolveOwnedEvolveBotRuntime(userId: string, botId: string, env?: string): Promise<EvolveBotRuntime | null> {
    const rows = await this.db.query<{
      active_engine: string | null; bot_type: string | null; bot_status: string | null; binding_id: string | number | null;
      device_provider: string | null; device_id: string | null;
      device_props: unknown; binding_status: string | null; env: string | null;
    }>(
      `SELECT b.active_engine, b.bot_type, b.status AS bot_status, b.binding_id,
              d.device_provider, d.device_id, d.device_props, d.status AS binding_status,
              COALESCE(d.env, b.env) AS env
       FROM ac_bots b
       LEFT JOIN ac_entity_device_binding d ON d.id = b.binding_id
       WHERE b.bot_id = ? AND (b.owner_id = ? OR b.entity_id = ?) AND b.is_delete = 0
         AND (? = '' OR COALESCE(d.env, b.env) = ?)
       ORDER BY b.id DESC
       LIMIT 1`,
      [botId, userId, userId, env ?? "", env ?? ""],
    ).catch(() => []);
    const row = rows[0];
    if (!row) return null;
    let deviceProps: Record<string, unknown> | null = null;
    if (row.device_props && typeof row.device_props === "object" && !Array.isArray(row.device_props)) {
      deviceProps = row.device_props as Record<string, unknown>;
    } else if (typeof row.device_props === "string" && row.device_props.trim()) {
      try {
        const parsed = JSON.parse(row.device_props) as unknown;
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          deviceProps = parsed as Record<string, unknown>;
        }
      } catch {
        // The target validator reports a missing ARCA instance id below. Do not
        // turn malformed provider metadata into a partially trusted target.
      }
    }
    const rawArcaInstanceId = deviceProps?.sandbox_id;
    const arcaInstanceId = typeof rawArcaInstanceId === "string" || typeof rawArcaInstanceId === "number"
      ? String(rawArcaInstanceId).trim() || null
      : null;
    const publishedRows = await this.db.query<{ published_count: number | string | Buffer }>(
      `SELECT COUNT(*) AS published_count
       FROM ac_bot_publish p
       JOIN ac_bots b ON b.id = p.source_bot_pk AND b.env = p.env
       WHERE b.bot_id = ? AND (b.owner_id = ? OR b.entity_id = ?)
         AND (? = '' OR b.env = ?)
         AND b.is_delete = 0 AND p.status = 'success'`,
      [botId, userId, userId, env ?? "", env ?? ""],
    ).catch(() => []);
    return {
      activeEngine: row.active_engine,
      botType: row.bot_type,
      hasServiceBot: Number(publishedRows[0]?.published_count ?? 0) > 0,
      botStatus: row.bot_status,
      bindingId: row.binding_id,
      provider: row.device_provider?.toLowerCase() ?? null,
      deviceId: row.device_id,
      arcaInstanceId,
      bindingStatus: row.binding_status,
      env: row.env,
    };
  }
  async findStep(stepId: string): Promise<EvolveStepRow | null> {
    return (await this.db.query<EvolveStepRow>(
      "SELECT * FROM ce_steps WHERE step_id = ?",
      [stepId],
    ))[0] ?? null;
  }
  async listSteps(taskId: string): Promise<EvolveStepRow[]> {
    return this.db.query<EvolveStepRow>(
      "SELECT * FROM ce_steps WHERE task_id = ? ORDER BY step_no",
      [taskId],
    );
  }

  async resolveInputSteps(step: EvolveStepRow): Promise<EvolveStepRow[]> {
    const all = await this.listSteps(step.task_id);
    const selected = all.filter((item) => item.step_no < step.step_no && item.status === "succeeded");
    const invalid = selected.find((item) =>
      item.step_id === step.step_id ||
      item.step_no >= step.step_no ||
      item.status !== "succeeded",
    );
    if (invalid) throw new Error(`无效的上游 Step: ${invalid.step_id}`);
    return selected;
  }

  async markDispatched(
    stepId: string,
    runId: string | null,
    sessionId: string | null,
    platformResponse?: unknown,
  ): Promise<void> {
    const step = await this.findStep(stepId);
    if (!step) throw new Error(`Step 不存在: ${stepId}`);
    const now = this.db.dialect.now();
    await this.db.transaction(async (tx) => {
      await tx.exec(
        `UPDATE ce_steps SET status = CASE WHEN status IN ('created', 'dispatching') THEN 'dispatched' ELSE status END, bot_run_id = ?,
         bot_session_id = ?, bot_response_json = ?, gmt_modified = ? WHERE step_id = ?`,
        [runId, sessionId, platformResponse == null ? null : JSON.stringify(platformResponse), now, stepId],
      );
      await tx.exec(
        `UPDATE ce_tasks
            SET status = CASE WHEN status IN ('pending', 'accepted', 'dispatched') THEN 'running' ELSE status END,
                error_message = CASE WHEN status IN ('pending', 'accepted', 'dispatched') THEN NULL ELSE error_message END,
                gmt_modified = ?
          WHERE task_id = ?`,
        [now, step.task_id],
      );
    });
  }

  async markExternalDispatched(stepId: string, jobId: string, response: unknown): Promise<void> {
    await this.markDispatched(stepId, jobId, null, response);
  }

  async markDispatchFailed(stepId: string, error: string): Promise<void> {
    const step = await this.findStep(stepId);
    if (!step) throw new Error(`Step 不存在: ${stepId}`);
    const now = this.db.dialect.now();
    const completedAt = this.db.dialect.now();
    await this.db.transaction(async (tx) => {
      await tx.exec(
        `UPDATE ce_steps SET status = 'failed', error_code = 'DISPATCH_FAILED',
         error_message = ?, retryable = 1, completed_at = ?, gmt_modified = ?
         WHERE step_id = ?`,
        [error, completedAt, now, stepId],
      );
      await tx.exec("UPDATE ce_tasks SET status = 'failed', error_message = ?, gmt_modified = ? WHERE task_id = ?", [error, now, step.task_id]);
    });
  }

  async claimCreatedBusinessStep(taskId: string): Promise<EvolveStepRow | null> {
    return this.db.transaction(async (tx) => {
      const candidate = (await tx.query<EvolveStepRow>(
        `SELECT * FROM ce_steps
         WHERE task_id = ? AND status = 'created' AND step_type <> 'skill_init'
         ORDER BY step_no, id LIMIT 1`,
        [taskId],
      ))[0];
      if (!candidate) return null;
      const result = await tx.exec(
        "UPDATE ce_steps SET status = 'dispatching', gmt_modified = ? WHERE step_id = ? AND status = 'created'",
        [tx.dialect.now(), candidate.step_id],
      );
      if (result.affectedRows !== 1) return null;
      return { ...candidate, status: "dispatching" };
    });
  }

  async claimSuggestionApplyStep(taskId: string, stepId: string): Promise<boolean> {
    const result = await this.db.exec(
      `UPDATE ce_steps
          SET status = 'running', started_at = ?, gmt_modified = ?
        WHERE task_id = ? AND step_id = ? AND step_type = 'suggestion_apply'
          AND status IN ('dispatching', 'dispatched')`,
      [this.db.dialect.now(), this.db.dialect.now(), taskId, stepId],
    );
    return result.affectedRows === 1;
  }

  async listActiveSuggestionApplySteps(): Promise<Array<{
    task_id: string;
    step_id: string;
    status: string;
    started_at: number | string | null;
    gmt_create: number | string;
    gmt_modified: number | string;
    output_json: string | null;
    config_json: string;
  }>> {
    return this.db.query(
      `SELECT t.task_id, s.step_id, s.status, s.started_at, s.gmt_create, s.gmt_modified, s.output_json, t.config_json
       FROM ce_steps s JOIN ce_tasks t ON t.task_id = s.task_id
       WHERE t.task_type = 'suggestion_apply' AND s.step_type = 'suggestion_apply'
         AND s.status IN ('created', 'dispatching', 'dispatched', 'running', 'applying')`,
    );
  }

  async tryFinalizeSuggestionApplication(stepId: string, input: {
    source: "callback" | "timeout";
    status: "succeeded" | "failed";
    summary: string;
    output?: Record<string, unknown>;
    errorCode?: string;
    errorMessage?: string;
    retryable?: boolean;
    suggestionIds: string[];
    workflowId: string;
    revisions?: Array<{ suggestionId: string; proposalDigest: string | null }>;
    actor: string;
    failureVerdict?: string;
    completedAtMs?: number;
  }): Promise<{ settled: boolean; supersededSuggestionIds: string[] }> {
    const now = this.db.dialect.now();
    const completedAt = this.db.dialect.now();
    return this.db.transaction(async (tx) => {
      const step = (await tx.query<{ task_id: string }>("SELECT task_id FROM ce_steps WHERE step_id = ? LIMIT 1", [stepId]))[0];
      if (!step) return { settled: false, supersededSuggestionIds: [] };
      const activePredicate = input.source === "callback"
        ? "status = 'running'"
        : "status IN ('created', 'dispatching', 'dispatched', 'running', 'applying')";
      const updated = await tx.exec(
        `UPDATE ce_steps SET status = ?, summary = ?, output_json = COALESCE(?, output_json),
                error_code = COALESCE(?, error_code), error_message = COALESCE(?, error_message),
                retryable = COALESCE(?, retryable), completed_at = ?, gmt_modified = ?
         WHERE step_id = ? AND ${activePredicate}`,
        [input.status, input.summary, input.output === undefined ? null : JSON.stringify(input.output),
          input.errorCode ?? null, input.errorMessage ?? null,
          input.retryable == null ? null : Number(input.retryable), completedAt, now, stepId],
      );
      if ((updated.affectedRows ?? 0) !== 1) return { settled: false, supersededSuggestionIds: [] };

      const supersededSuggestionIds: string[] = [];
      const actionTimestamp = new Date(input.completedAtMs ?? Date.now()).toISOString();
      const appliedAt = Math.floor((input.completedAtMs ?? Date.now()) / 1000);
      for (const suggestionId of input.suggestionIds) {
        const suggestion = (await tx.query<EvolveSuggestionRow>(
          "SELECT * FROM workflow_healing_suggestions WHERE id = ? LIMIT 1",
          [suggestionId],
        ))[0] ?? null;
        const revision = input.revisions?.find((item) => item.suggestionId === suggestionId);
        const expectedProposalDigest = revision?.proposalDigest ?? null;
        const superseded = input.status === "succeeded"
          && expectedProposalDigest !== (suggestion?.proposal_digest ?? null);
        if (superseded) supersededSuggestionIds.push(suggestionId);

        if (suggestion && input.status === "succeeded" && !superseded) {
          if (!new Set(["adopted", "applying"]).has(suggestion.status)) {
            throw new Error(`当前建议状态为 ${suggestion.status}，只能记录已采纳或应用中的建议`);
          }
          const actionLog = this.parseActionLog(suggestion.action_log);
          actionLog.push({
            action: "applied_unverified",
            actor: input.actor,
            note: input.summary,
            timestamp: actionTimestamp,
          });
          await tx.exec(
            `UPDATE workflow_healing_suggestions
             SET status = 'applied_unverified', applied_at = ?, verification_status = 'observing',
                 verification_checked_at = NULL, recurrence_count = 0, last_recurrence_at = NULL,
                 action_log = ?, updated_by = ?, gmt_modified = ?
             WHERE id = ?`,
            [appliedAt, JSON.stringify(actionLog), input.actor, now, suggestionId],
          );
        } else if (suggestion && input.status === "failed") {
          const actionLog = this.parseActionLog(suggestion.action_log);
          actionLog.push({ action: "failed", actor: input.actor, note: input.summary, timestamp: actionTimestamp });
          await tx.exec(
            `UPDATE workflow_healing_suggestions
             SET status = 'failed', action_log = ?, updated_by = ?, gmt_modified = ? WHERE id = ?`,
            [JSON.stringify(actionLog), input.actor, now, suggestionId],
          );
        }

        const succeeded = input.status === "succeeded";
        await tx.exec(
          `INSERT INTO workflow_healing_outcomes
           (outcome_id, lesson_id, suggestion_id, workflow_id, node_id, action, applied, succeeded,
            verdict, note, source_task_id, source_step_id, created_by, gmt_create, gmt_modified)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
          [
            `OUT-SUGG-${randomUUID().slice(0, 12).toUpperCase()}`, null, suggestionId,
            input.workflowId || null, suggestion?.node_id ?? null, "suggestion_apply",
            succeeded && !superseded ? 1 : 0, succeeded ? 1 : 0,
            succeeded
              ? superseded ? "application_succeeded_superseded" : "application_succeeded"
              : input.failureVerdict ?? "application_failed",
            input.summary, step.task_id, stepId, input.actor, now, now,
          ],
        );
      }

      await tx.exec(
        "UPDATE ce_tasks SET status = ?, error_message = ?, gmt_modified = ? WHERE task_id = ?",
        [input.status === "succeeded" ? "completed" : "failed",
          input.status === "succeeded" ? null : input.errorMessage ?? input.summary, now, step.task_id],
      );
      return { settled: true, supersededSuggestionIds };
    });
  }

  async tryUpdateSuggestionApplyProgress(stepId: string, summary: string, output: Record<string, unknown>): Promise<boolean> {
    const updated = await this.db.exec(
      `UPDATE ce_steps SET summary = ?, output_json = ?, gmt_modified = ?
       WHERE step_id = ? AND step_type = 'suggestion_apply' AND status = 'running'`,
      [summary, JSON.stringify(output), this.db.dialect.now(), stepId],
    );
    return (updated.affectedRows ?? 0) === 1;
  }

  async updateStepStatus(stepId: string, input: {
    status: string; summary?: string; output?: Record<string, unknown>;
    errorCode?: string; errorMessage?: string; retryable?: boolean;
  }): Promise<void> {
    const step = await this.findStep(stepId);
    if (!step) throw new Error(`Step 不存在: ${stepId}`);
    const now = this.db.dialect.now();
    const lifecycleAt = this.db.dialect.now();
    const terminal = ["succeeded", "failed", "canceled"].includes(input.status);
    await this.db.transaction(async (tx) => {
      await tx.exec(
        `UPDATE ce_steps SET status = ?, summary = COALESCE(?, summary),
         output_json = COALESCE(?, output_json), error_code = COALESCE(?, error_code),
         error_message = COALESCE(?, error_message), retryable = COALESCE(?, retryable),
         started_at = COALESCE(started_at, ?), completed_at = ?, gmt_modified = ?
         WHERE step_id = ?`,
        [input.status, input.summary ?? null,
          input.output === undefined ? null : JSON.stringify(input.output),
          input.errorCode ?? null, input.errorMessage ?? null,
          input.retryable == null ? null : Number(input.retryable),
          lifecycleAt, terminal ? lifecycleAt : null, now, stepId],
      );
      if (input.status === "failed" || input.status === "canceled") {
        await tx.exec(
          "UPDATE ce_tasks SET status = ?, error_message = ?, gmt_modified = ? WHERE task_id = ?",
          [input.status === "canceled" ? "canceled" : "failed", input.errorMessage ?? input.status, now, step.task_id],
        );
      }
    });
  }

  async recordCancellationAttempt(stepId: string, input: {
    status: "remote_stopped" | "remote_stop_failed";
    transport?: string | null;
    error?: string | null;
  }): Promise<void> {
    const step = await this.findStep(stepId);
    if (!step) throw new Error(`Step 不存在: ${stepId}`);
    let botResponse: Record<string, unknown> = {};
    if (step.bot_response_json) {
      try {
        const parsed = JSON.parse(step.bot_response_json) as unknown;
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          botResponse = parsed as Record<string, unknown>;
        }
      } catch {
        botResponse = { original_response: step.bot_response_json };
      }
    }
    botResponse.evolve_cancel = {
      status: input.status,
      transport: input.transport ?? null,
      error: input.error ?? null,
      recordedAt: new Date().toISOString(),
    };
    await this.db.exec(
      "UPDATE ce_steps SET bot_response_json = ?, gmt_modified = ? WHERE step_id = ?",
      [JSON.stringify(botResponse), this.db.dialect.now(), stepId],
    );
  }

  async reviseSucceededStep(stepId: string, input: {
    summary?: string;
    output?: Record<string, unknown>;
  }): Promise<void> {
    const step = await this.findStep(stepId);
    if (!step) throw new Error(`Step 不存在: ${stepId}`);
    if (step.status !== "succeeded") throw new Error(`只有 succeeded Step 可以修订交付物: ${step.status}`);
    const now = this.db.dialect.now();
    await this.db.exec(
      `UPDATE ce_steps SET summary = COALESCE(?, summary),
       output_json = COALESCE(?, output_json), gmt_modified = ? WHERE step_id = ?`,
      [input.summary ?? null,
        input.output === undefined ? null : JSON.stringify(input.output),
        now, stepId],
    );
  }

  async completeTask(taskId: string): Promise<void> {
    const now = this.db.dialect.now();
    await this.db.exec(
      "UPDATE ce_tasks SET status = 'completed', error_message = NULL, gmt_modified = ? WHERE task_id = ?",
      [now, taskId],
    );
  }
}
