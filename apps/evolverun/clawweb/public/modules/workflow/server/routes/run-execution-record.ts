import type { Request, Response, Router } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { AppConfigRepository } from "../repositories/app-config-repository.js";
import { verifyExecutionRecordToken } from "../services/execution-record-token-service.js";
import type { FlowRunRepository } from "../repositories/flow-run-repository.js";
import type { NodeExecutionRepository } from "../repositories/node-execution-repository.js";
import { isFanoutExecutionRecord, readExecutionRecord } from "../services/execution-record-service.js";
import { FanoutReadError } from "../contracts/run-subworkflows.js";
import { captureSensitiveApiError } from "./error-log-format.js";

/** null denotes invalid input; inspect raw keys so simple/extended parsers reject objects alike. */
function parseIncludeNodeOutput(req: Request): boolean | null {
  const params = new URL(req.originalUrl, "http://localhost").searchParams;
  const entries = [...params].filter(([key]) => key === "includeNodeOutput" || key.startsWith("includeNodeOutput["));
  if (entries.length === 0) return false;
  if (entries.length !== 1 || entries[0][0] !== "includeNodeOutput") return null;
  if (entries[0][1] === "true") return true;
  if (entries[0][1] === "false") return false;
  return null;
}

function rejectExecutionRecord(res: Response, status: number, error: string, message: string): void {
  if (status >= 500) {
    // SQL, credentials and opaque payloads may appear in caught exceptions. Observe, never inspect them.
    console.error("[runs] execution record failed:", { stack: captureSensitiveApiError(res), stackSource: "observation" });
  } else {
    console.warn("[runs] execution record rejected:", { status });
  }
  res.status(status).json({ error, message });
}

export function registerRunExecutionRecord(
  router: Router,
  flowRunRepo: FlowRunRepository | null,
  nodeExecRepo: NodeExecutionRepository | null,
  options: { executionRecordConfigRepo?: Pick<AppConfigRepository, "findByKey"> | null; restrictFanoutPayloads: boolean },
): void {
  router.get("/:flowId/execution-record", asyncHandler(async (req, res) => {
    res.set("Cache-Control", "no-store");
    try {
      const flowId = typeof req.params.flowId === "string" ? req.params.flowId.trim() : "";
      const includeNodeOutput = parseIncludeNodeOutput(req);
      if (!flowId || includeNodeOutput === null) {
        rejectExecutionRecord(res, 400, "Bad Request", !flowId ? "flowId must be nonblank" : "includeNodeOutput must be a single true or false value");
        return;
      }
      const auth = await verifyExecutionRecordToken(options.executionRecordConfigRepo, req.headers.authorization, req.rawHeaders);
      if (auth === "unavailable") {
        rejectExecutionRecord(res, 503, "Service Unavailable", "Execution record authentication unavailable");
        return;
      }
      if (auth !== "authenticated") {
        rejectExecutionRecord(res, 401, "Unauthorized", "Invalid execution record token");
        return;
      }
      if (!flowRunRepo || !nodeExecRepo) {
        rejectExecutionRecord(res, 503, "Service Unavailable", "Database not configured");
        return;
      }
      const run = await flowRunRepo.findExecutionRecordByFlowIdStrict(flowId);
      if (!run) {
        rejectExecutionRecord(res, 404, "Not Found", `Flow ${flowId} not found`);
        return;
      }
      if (run.flow_id !== flowId) {
        rejectExecutionRecord(res, 409, "Conflict", "Run identity changed; retry the request");
        return;
      }
      // Inspect only the selected state, not inspectRun: that reader also enforces view ACL.
      if (options.restrictFanoutPayloads && isFanoutExecutionRecord(run)) {
        rejectExecutionRecord(res, 403, "Forbidden", "Execution records are unavailable for fanout runs");
        return;
      }
      const workflow = await flowRunRepo.readExecutionRecordWorkflow(run);
      res.json(await readExecutionRecord(run, nodeExecRepo, includeNodeOutput, workflow));
    } catch (error) {
      if (error instanceof FanoutReadError && error.code === "identity_conflict") {
        rejectExecutionRecord(res, 409, "Conflict", "Run identity changed; retry the request");
      } else rejectExecutionRecord(res, 500, "Internal Server Error", "Failed to load execution record");
    }
  }));
}
