/**
 * Repository for cm_bench_task_results table — individual benchmark task results.
 */
import type { IDatabase } from "../db.js";

export type BenchTaskResultRow = {
  id: number;
  result_id: string;
  bench_run_id: string;
  task_id: string;
  task_name: string | null;
  status: string;
  score: number | null;
  max_score: number | null;
  grading_type: string | null;
  execution_time_ms: number | null;
  transcript_path: string | null;
  workspace_path: string | null;
  result_json: string | null;
  breakdown_json: string | null;
  notes: string | null;
  error_text: string | null;
  gmt_create: number;
  gmt_modified: number;
};

export type CreateBenchTaskResultInput = {
  resultId: string;
  benchRunId: string;
  taskId: string;
  taskName?: string | null;
  status: string;
  score?: number | null;
  maxScore?: number | null;
  gradingType?: string | null;
  executionTimeMs?: number | null;
  transcriptPath?: string | null;
  workspacePath?: string | null;
  resultJson?: string | null;
  breakdownJson?: string | null;
  notes?: string | null;
  errorText?: string | null;
};

const SELECT_COLUMNS = `id, result_id, bench_run_id, task_id, task_name, status, score, max_score, grading_type, execution_time_ms, transcript_path, workspace_path, result_json, breakdown_json, notes, error_text, gmt_create, gmt_modified`;

export class BenchTaskResultRepository {
  constructor(private db: IDatabase) {}

  async listByBenchRunId(benchRunId: string): Promise<BenchTaskResultRow[]> {
    return this.db.query<BenchTaskResultRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_task_results WHERE bench_run_id = ? ORDER BY gmt_create ASC`,
      [benchRunId],
    );
  }

  async findByResultId(resultId: string): Promise<BenchTaskResultRow | null> {
    const rows = await this.db.query<BenchTaskResultRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_bench_task_results WHERE result_id = ?`,
      [resultId],
    );
    return rows[0] ?? null;
  }

  async create(input: CreateBenchTaskResultInput): Promise<BenchTaskResultRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO cm_bench_task_results (result_id, bench_run_id, task_id, task_name, status, score, max_score, grading_type, execution_time_ms, transcript_path, workspace_path, result_json, breakdown_json, notes, error_text, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        input.resultId,
        input.benchRunId,
        input.taskId,
        input.taskName ?? null,
        input.status,
        input.score ?? null,
        input.maxScore ?? null,
        input.gradingType ?? null,
        input.executionTimeMs ?? null,
        input.transcriptPath ?? null,
        input.workspacePath ?? null,
        input.resultJson ?? null,
        input.breakdownJson ?? null,
        input.notes ?? null,
        input.errorText ?? null,
        now,
        now,
      ],
    );
    const result = await this.findByResultId(input.resultId);
    return result!;
  }

  async upsert(input: CreateBenchTaskResultInput): Promise<BenchTaskResultRow> {
    const existing = await this.findByResultId(input.resultId);
    if (!existing) return this.create(input);

    const now = this.db.dialect.now();
    await this.db.exec(
      `UPDATE cm_bench_task_results
       SET bench_run_id = ?, task_id = ?, task_name = ?, status = ?, score = ?, max_score = ?,
           grading_type = ?, execution_time_ms = ?, transcript_path = ?, workspace_path = ?,
           result_json = ?, breakdown_json = ?, notes = ?, error_text = ?, gmt_modified = ?
       WHERE result_id = ?`,
      [
        input.benchRunId,
        input.taskId,
        input.taskName ?? null,
        input.status,
        input.score ?? null,
        input.maxScore ?? null,
        input.gradingType ?? null,
        input.executionTimeMs ?? null,
        input.transcriptPath ?? null,
        input.workspacePath ?? null,
        input.resultJson ?? null,
        input.breakdownJson ?? null,
        input.notes ?? null,
        input.errorText ?? null,
        now,
        input.resultId,
      ],
    );
    const result = await this.findByResultId(input.resultId);
    return result!;
  }

  async batchCreate(inputs: CreateBenchTaskResultInput[]): Promise<BenchTaskResultRow[]> {
    return this.db.transaction(async (txDb) => {
      const txRepo = new BenchTaskResultRepository(txDb);
      const results: BenchTaskResultRow[] = [];
      for (const input of inputs) {
        const row = await txRepo.upsert(input);
        results.push(row);
      }
      return results;
    });
  }
}
