import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { skillAuditTransaction } from "./skill-audit.js";

export type ReportResponse = { statusCode: number; body: Record<string, unknown> };
type Receipt = { digest: string; response?: ReportResponse };
type ReportStep = { task_id: string; status: string; ext_json: string | null };
type Claim = { claimed: true } | { claimed: false; response: ReportResponse };

/** Delivery receipts live with the Step, including after HITL resumes it.
 * A pending receipt is deliberately never stolen: orchestration may have run
 * before the sender lost its connection. No database lock spans dispatch I/O. */
export class StepReportRepository {
  constructor(private readonly db: IDatabase) {}

  private async locked<T>(taskId: string, stepId: string,
    work: (tx: IDatabase, step: ReportStep | undefined, ext: Record<string, unknown>, receipts: Record<string, Receipt>) => Promise<T>,
  ): Promise<T> {
    return skillAuditTransaction(this.db, async (tx) => {
      await tx.exec("UPDATE ce_tasks SET task_id = task_id WHERE task_id = ?", [taskId]);
      const lock = tx.dbType === "mysql" || tx.dbType === "zdas" ? " FOR UPDATE" : "";
      const step = (await tx.query<ReportStep>(
        `SELECT task_id, status, ext_json FROM ce_steps WHERE step_id = ?${lock}`, [stepId],
      ))[0];
      const ext = (JSON.parse(step?.ext_json ?? "{}") ?? {}) as Record<string, unknown>;
      const receipts = (ext.report_delivery ?? {}) as Record<string, Receipt>;
      return work(tx, step, ext, receipts);
    });
  }

  async claim(taskId: string, stepId: string, reportId: string, digest: string): Promise<Claim> {
    return this.locked(taskId, stepId, async (tx, step, ext, receipts) => {
      const reject = (statusCode: number, error: string): Claim => ({ claimed: false, response: { statusCode, body: { error } } });
      if (!step || step.task_id !== taskId) return reject(404, "step 不属于指定 task");
      const existing = receipts[reportId];
      if (existing) {
        if (existing.digest !== digest) return reject(409, "同一回报标识的内容不能改变");
        if (existing.response) return { claimed: false, response: existing.response };
        return reject(503, "回报仍在处理或处理结果尚未确认；不会重复执行");
      }
      if (Object.values(receipts).some((item) => !item.response)) {
        return reject(503, "上一条回报的处理结果尚未确认");
      }
      if (["succeeded", "failed", "canceled", "waiting_context"].includes(step.status)) {
        return reject(409, "Step 已结束或正在等待交互，不能接收新的回报");
      }
      receipts[reportId] = { digest };
      await tx.exec("UPDATE ce_steps SET ext_json = ?, gmt_modified = ? WHERE step_id = ?", [
        JSON.stringify({ ...ext, report_delivery: receipts }), tx.dialect.now(), stepId,
      ]);
      return { claimed: true };
    });
  }

  async complete(taskId: string, stepId: string, reportId: string, response: ReportResponse): Promise<void> {
    await this.locked(taskId, stepId, async (tx, step, ext, receipts) => {
      if (!step || step.task_id !== taskId || !receipts[reportId]) throw new Error("Step report receipt missing");
      // Keep only the acknowledgement, not another copy of large form content.
      const body = { ...response.body };
      if (body.interaction && typeof body.interaction === "object") {
        const { question: _question, ...reference } = body.interaction as Record<string, unknown>;
        body.interaction = reference;
      }
      receipts[reportId] = { ...receipts[reportId], response: { ...response, body } };
      await tx.exec("UPDATE ce_steps SET ext_json = ?, gmt_modified = ? WHERE step_id = ?", [
        JSON.stringify({ ...ext, report_delivery: receipts }), tx.dialect.now(), stepId,
      ]);
    });
  }
}
