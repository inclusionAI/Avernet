import { createHash } from "node:crypto";
import type { Request, Response } from "express";
import type { EvolveRepository } from "../repositories/evolve-repository.js";
import type { ReportResponse } from "../repositories/step-report-repository.js";

type ReportHandler = (req: Request, res: Response) => Promise<unknown>;

/** New Runners opt in; legacy reporters keep their existing lifecycle semantics. */
export function withStepReportDelivery(repo: EvolveRepository | null, handler: ReportHandler): ReportHandler {
  return async (req, res) => {
    const id = req.header("X-ClawEvolve-Report-Id");
    if (!id || !repo) return handler(req, res);
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) {
      res.status(400).json({ error: "无效的回报标识" }); return;
    }
    const taskId = String(req.params.taskId);
    const stepId = String(req.params.stepId);
    const digest = createHash("sha256").update(JSON.stringify(req.body ?? {})).digest("hex");
    const claim = await repo.stepReports.claim(taskId, stepId, id, digest);
    if (!claim.claimed) {
      res.status(claim.response.statusCode).json(claim.response.body); return;
    }
    // The handler finishes all state changes and scheduling before an ACK is
    // persisted and emitted. A dropped response then replays this receipt only.
    let captured: ReportResponse | undefined;
    let statusCode = 200;
    const response = new Proxy(res, {
      get(target, key) {
        if (key === "status") return (code: number) => { statusCode = code; return response; };
        if (key === "json") return (body: Record<string, unknown>) => {
          captured = { statusCode, body }; return response;
        };
        return Reflect.get(target, key);
      },
    });
    await handler(req, response);
    if (!captured) throw new Error("Step report handler did not return a response");
    // 5xx may follow partial side effects. Leave the claim uncertain instead of
    // re-entering business orchestration on a transport retry.
    if (captured.statusCode < 500) await repo.stepReports.complete(taskId, stepId, id, captured);
    res.status(captured.statusCode).json(captured.body);
  };
}
