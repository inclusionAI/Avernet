/**
 * Repository for cm_bench_runs table — benchmark run instances.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type BenchRunRow = {
  id: number;
  bench_run_id: string;
  domain_id: string;
  template_name: string;
  template_version: number;
  target_type: string;
  status: string;
  score: number | null;
  max_score: number | null;
  pass_rate: number | null;
  model: string | null;
  suite: string | null;
  scene: string | null;
  triggered_by: string | null;
  clawmind_flow_id: string | null;
  session_id: string | null;
  session_key: string | null;
  run_config_json: string | null;
  summary_json: string | null;
  error_text: string | null;
  started_at: number | null;
  completed_at: number | null;
  owner_user_id: string;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateBenchRunInput = {
  benchRunId: string;
  domainId: string;
  templateName: string;
  templateVersion: number;
  targetType?: string;
  model?: string | null;
  suite?: string | null;
  scene?: string | null;
  triggeredBy?: string | null;
  clawmindFlowId?: string | null;
  sessionId?: string | null;
  sessionKey?: string | null;
  runConfigJson?: string | null;
  startedAt?: number | null;
  status?: string;
  ownerUserId?: string;
};

export type UpdateBenchRunInput = {
  status?: string;
  score?: number | null;
  maxScore?: number | null;
  passRate?: number | null;
  model?: string | null;
  suite?: string | null;
  scene?: string | null;
  triggeredBy?: string | null;
  clawmindFlowId?: string | null;
  sessionId?: string | null;
  sessionKey?: string | null;
  runConfigJson?: string | null;
  summaryJson?: string | null;
  errorText?: string | null;
  startedAt?: number | null;
  completedAt?: number | null;
  ownerUserId?: string;
};

const SELECT_COLUMNS = `id, bench_run_id, domain_id, template_name, template_version, target_type, status, score, max_score, pass_rate, model, suite, scene, triggered_by, clawmind_flow_id, session_id, session_key, run_config_json, summary_json, error_text, started_at, completed_at, owner_user_id, gmt_create, gmt_modified`;

export class BenchRunRepository {
  constructor(private db: IDatabase) {}

  async listAll(filters?: {
    ownerUserId?: string;
    domainId?: string;
    templateName?: string;
    status?: string;
    model?: string;
    suite?: string;
    scene?: string;
    clawmindFlowId?: string;
    startedFrom?: number;
    startedTo?: number;
    limit?: number;
    offset?: number;
  }): Promise<BenchRunRow[]> {
    const conditions: string[] = [];
    const values: unknown[] = [];

    if (filters?.ownerUserId) {
      conditions.push("owner_user_id = ?");
      values.push(filters.ownerUserId);
    }
    if (filters?.domainId) {
      conditions.push("domain_id = ?");
      values.push(filters.domainId);
    }
    if (filters?.templateName) {
      conditions.push("template_name = ?");
      values.push(filters.templateName);
    }
    if (filters?.status) {
      conditions.push("status = ?");
      values.push(filters.status);
    }
    if (filters?.model) {
      conditions.push("model = ?");
      values.push(filters.model);
    }
    if (filters?.suite) {
      conditions.push("suite = ?");
      values.push(filters.suite);
    }
    if (filters?.scene) {
      conditions.push("scene = ?");
      values.push(filters.scene);
    }
    if (filters?.clawmindFlowId) {
      conditions.push("clawmind_flow_id = ?");
      values.push(filters.clawmindFlowId);
    }
    if (filters?.startedFrom !== undefined) {
      conditions.push("started_at >= ?");
      values.push(filters.startedFrom);
    }
    if (filters?.startedTo !== undefined) {
      conditions.push("started_at <= ?");
      values.push(filters.startedTo);
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
    const limit = filters?.limit ?? 50;
    const offset = filters?.offset ?? 0;
    const sql = `SELECT ${SELECT_COLUMNS} FROM cm_bench_runs ${where} ORDER BY gmt_create DESC LIMIT ${limit} OFFSET ${offset}`;
    return this.db.query<BenchRunRow>(sql, values);
  }

  async count(filters?: {
    ownerUserId?: string;
    domainId?: string;
    templateName?: string;
    status?: string;
    model?: string;
    suite?: string;
    scene?: string;
    clawmindFlowId?: string;
    startedFrom?: number;
    startedTo?: number;
  }): Promise<number> {
    const conditions: string[] = [];
    const values: unknown[] = [];

    if (filters?.ownerUserId) {
      conditions.push("owner_user_id = ?");
      values.push(filters.ownerUserId);
    }
    if (filters?.domainId) {
      conditions.push("domain_id = ?");
      values.push(filters.domainId);
    }
    if (filters?.templateName) {
      conditions.push("template_name = ?");
      values.push(filters.templateName);
    }
    if (filters?.status) {
      conditions.push("status = ?");
      values.push(filters.status);
    }
    if (filters?.model) {
      conditions.push("model = ?");
      values.push(filters.model);
    }
    if (filters?.suite) {
      conditions.push("suite = ?");
      values.push(filters.suite);
    }
    if (filters?.scene) {
      conditions.push("scene = ?");
      values.push(filters.scene);
    }
    if (filters?.clawmindFlowId) {
      conditions.push("clawmind_flow_id = ?");
      values.push(filters.clawmindFlowId);
    }
    if (filters?.startedFrom !== undefined) {
      conditions.push("started_at >= ?");
      values.push(filters.startedFrom);
    }
    if (filters?.startedTo !== undefined) {
      conditions.push("started_at <= ?");
      values.push(filters.startedTo);
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
    const sql = `SELECT COUNT(*) as cnt FROM cm_bench_runs ${where}`;
    const rows = await this.db.query<{ cnt: number }>(sql, values);
    return rows[0]?.cnt ?? 0;
  }

  async findLatestByOwnerAndDomain(ownerUserId: string, domainId: string): Promise<BenchRunRow | null> {
    const rows = await this.db.query<BenchRunRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_runs WHERE owner_user_id = ? AND domain_id = ? ORDER BY gmt_create DESC LIMIT 1`,
      [ownerUserId, domainId],
    );
    return rows[0] ?? null;
  }

  async findByBenchRunId(benchRunId: string): Promise<BenchRunRow | null> {
    const rows = await this.db.query<BenchRunRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_runs WHERE bench_run_id = ?`,
      [benchRunId],
    );
    return rows[0] ?? null;
  }

  async create(input: CreateBenchRunInput): Promise<BenchRunRow> {
    const now = this.db.dialect.now();
    const ownerUserId = input.ownerUserId ?? "";
    const startedAt = input.startedAt ?? Math.floor(Date.now() / 1000);
    await this.db.exec(
      `INSERT INTO cm_bench_runs (bench_run_id, domain_id, template_name, template_version, target_type, status, model, suite, scene, triggered_by, clawmind_flow_id, session_id, session_key, run_config_json, started_at, owner_user_id, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.benchRunId,
        input.domainId,
        input.templateName,
        input.templateVersion,
        input.targetType ?? "agent_session",
        input.status ?? "running",
        input.model ?? null,
        input.suite ?? null,
        input.scene ?? null,
        input.triggeredBy ?? null,
        input.clawmindFlowId ?? null,
        input.sessionId ?? null,
        input.sessionKey ?? null,
        input.runConfigJson ?? null,
        startedAt,
        ownerUserId,
        now,
        now,
      ],
    );
    const result = await this.findByBenchRunId(input.benchRunId);
    return result!;
  }

  async update(benchRunId: string, input: UpdateBenchRunInput): Promise<BenchRunRow | null> {
    const existing = await this.findByBenchRunId(benchRunId);
    if (!existing) return null;

    const now = this.db.dialect.now();
    const sets: string[] = [];
    const values: unknown[] = [];

    const fields: Array<[string, unknown]> = [
      ["status", input.status],
      ["score", input.score],
      ["max_score", input.maxScore],
      ["pass_rate", input.passRate],
      ["model", input.model],
      ["suite", input.suite],
      ["scene", input.scene],
      ["triggered_by", input.triggeredBy],
      ["clawmind_flow_id", input.clawmindFlowId],
      ["session_id", input.sessionId],
      ["session_key", input.sessionKey],
      ["run_config_json", input.runConfigJson],
      ["summary_json", input.summaryJson],
      ["error_text", input.errorText],
      ["started_at", input.startedAt],
      ["completed_at", input.completedAt],
    ];

    for (const [col, val] of fields) {
      if (val !== undefined) {
        sets.push(`${col} = ?`);
        values.push(val);
      }
    }

    if (sets.length === 0) return existing;

    sets.push("gmt_modified = ?");
    values.push(now);
    values.push(benchRunId);

    await this.db.exec(
      `UPDATE cm_bench_runs SET ${sets.join(", ")} WHERE bench_run_id = ?`,
      values,
    );
    return this.findByBenchRunId(benchRunId);
  }
}
