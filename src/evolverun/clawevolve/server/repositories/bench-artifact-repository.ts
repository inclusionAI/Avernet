/**
 * Repository for cm_bench_artifacts table — benchmark run artifacts.
 */
import type { IDatabase } from "../db.js";

export type BenchArtifactRow = {
  id: number;
  artifact_id: string;
  bench_run_id: string;
  result_id: string | null;
  task_id: string | null;
  artifact_type: string;
  filename: string | null;
  content_type: string | null;
  size_bytes: number | null;
  storage_type: string;
  storage_path: string | null;
  content_text: string | null;
  content_json: string | null;
  summary_json: string | null;
  sha256: string | null;
  created_by: string | null;
  owner_user_id: string;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateBenchArtifactInput = {
  artifactId: string;
  benchRunId: string;
  resultId?: string | null;
  taskId?: string | null;
  artifactType: string;
  filename?: string | null;
  contentType?: string | null;
  sizeBytes?: number | null;
  storageType?: string;
  storagePath?: string | null;
  contentText?: string | null;
  contentJson?: string | null;
  summaryJson?: string | null;
  sha256?: string | null;
  createdBy?: string | null;
  ownerUserId: string;
};

const SELECT_COLUMNS = `id, artifact_id, bench_run_id, result_id, task_id, artifact_type, filename, content_type, size_bytes, storage_type, storage_path, content_text, content_json, summary_json, sha256, created_by, owner_user_id, gmt_create, gmt_modified`;

export class BenchArtifactRepository {
  constructor(private db: IDatabase) {}

  async create(input: CreateBenchArtifactInput): Promise<BenchArtifactRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO cm_bench_artifacts (artifact_id, bench_run_id, result_id, task_id, artifact_type, filename, content_type, size_bytes, storage_type, storage_path, content_text, content_json, summary_json, sha256, created_by, owner_user_id, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.artifactId,
        input.benchRunId,
        input.resultId ?? null,
        input.taskId ?? null,
        input.artifactType,
        input.filename ?? null,
        input.contentType ?? null,
        input.sizeBytes ?? null,
        input.storageType ?? "db",
        input.storagePath ?? null,
        input.contentText ?? null,
        input.contentJson ?? null,
        input.summaryJson ?? null,
        input.sha256 ?? null,
        input.createdBy ?? null,
        input.ownerUserId,
        now,
        now,
      ],
    );
    const row = await this.findByArtifactId(input.artifactId);
    return row!;
  }

  async findByArtifactId(artifactId: string): Promise<BenchArtifactRow | null> {
    const rows = await this.db.query<BenchArtifactRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_artifacts WHERE artifact_id = ?`,
      [artifactId],
    );
    return rows[0] ?? null;
  }

  async listByBenchRunId(filters: {
    benchRunId: string;
    artifactType?: string;
    taskId?: string;
    includeContent?: boolean;
  }): Promise<BenchArtifactRow[]> {
    const conditions = ["bench_run_id = ?"];
    const values: unknown[] = [filters.benchRunId];
    if (filters.artifactType) {
      conditions.push("artifact_type = ?");
      values.push(filters.artifactType);
    }
    if (filters.taskId) {
      conditions.push("task_id = ?");
      values.push(filters.taskId);
    }
    const columns = filters.includeContent
      ? SELECT_COLUMNS
      : SELECT_COLUMNS.replace(", content_text, content_json", ", NULL AS content_text, NULL AS content_json");
    return this.db.query<BenchArtifactRow>(
      `SELECT ${columns} FROM cm_bench_artifacts WHERE ${conditions.join(" AND ")} ORDER BY gmt_create ASC`,
      values,
    );
  }
}
