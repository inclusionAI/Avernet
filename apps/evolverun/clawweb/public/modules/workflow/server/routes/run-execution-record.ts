import type { Request, Response, Router } from "express";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";
import type { FlowRunRepository } from "../repositories/flow-run-repository.js";
import type { NodeExecutionRepository } from "../repositories/node-execution-repository.js";
import { readExecutionRecord } from "../services/execution-record-service.js";
import { FanoutReadError, type FanoutReadErrorCode, type RunSubworkflowsReader } from "../contracts/run-subworkflows.js";
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


const readerFailures: Record<FanoutReadErrorCode, { status: number; error: string; message: string }> = {
  unauthenticated: { status: 401, error: "Unauthorized", message: "User identity required" },
  forbidden: { status: 403, error: "Forbidden", message: "No view permission for this run" },
  not_found: { status: 404, error: "Not Found", message: "Run not found" },
  invalid_query: { status: 400, error: "Bad Request", message: "Invalid execution record request" },
  identity_conflict: { status: 409, error: "Conflict", message: "Run identity changed; retry the request" },
  snapshot_changed: { status: 409, error: "Conflict", message: "Run identity changed; retry the request" },
  invalid_state: { status: 500, error: "Internal Server Error", message: "Failed to load execution record" },
  unavailable: { status: 503, error: "Service Unavailable", message: "Execution record unavailable" },
};

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
  botPermRepo: Pick<BotWorkflowPermissionRepository, "resolveRunViewScope"> | null,
  runSubviews: RunSubworkflowsReader | null = null,
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
      if (!flowRunRepo || !nodeExecRepo) {
        rejectExecutionRecord(res, 503, "Service Unavailable", "Database not configured");
        return;
      }
      const actor = req.isAdmin || (!botPermRepo && !runSubviews) ? null : resolveWorkflowActorId(req);
      if ((botPermRepo || runSubviews) && !req.isAdmin && !actor) {
        rejectExecutionRecord(res, 401, "Unauthorized", "User identity required");
        return;
      }
      // The host reader owns authorization and fanout visibility, even without botPermRepo.
      const inspection = runSubviews
        ? await runSubviews.inspectRun({ actorId: actor, isAdmin: req.isAdmin === true }, flowId)
        : null;
      if (inspection && (inspection.managed || inspection.restricted || inspection.sensitiveChild)) {
        rejectExecutionRecord(res, 403, "Forbidden", "Execution records are unavailable for fanout runs");
        return;
      }
      const run = await flowRunRepo.findExecutionRecordByFlowIdStrict(flowId);
      if (!run) {
        rejectExecutionRecord(res, 404, "Not Found", `Flow ${flowId} not found`);
        return;
      }
      // Pin the raw selection to the exact identity the reader authorized before reading node payloads.
      if (run.flow_id !== flowId || (inspection && (inspection.run.flow_id !== run.flow_id
        || inspection.run.workflow_id !== run.workflow_id || inspection.run.origin_bot_id !== run.origin_bot_id))) {
        rejectExecutionRecord(res, 409, "Conflict", "Run identity changed; retry the request");
        return;
      }
      if (botPermRepo && !req.isAdmin && actor) {
        const scope = await botPermRepo.resolveRunViewScope(run.workflow_id, actor);
        const allowed = scope === "all" || (typeof scope !== "string" && Boolean(run.origin_bot_id) && scope.bots.some(bot => (
          bot.ownerId === "*"
            ? run.origin_bot_id.startsWith(`${bot.botId}:`)
              && run.origin_bot_id.slice(bot.botId.length + 1).trim() !== ""
            : run.origin_bot_id === `${bot.botId}:${bot.ownerId}`
        )));
        if (!allowed) {
          rejectExecutionRecord(res, 403, "Forbidden", "No view permission for this run");
          return;
        }
      }
      res.json(await readExecutionRecord(run, nodeExecRepo, includeNodeOutput));
    } catch (error) {
      const failure = error instanceof FanoutReadError ? readerFailures[error.code] : undefined;
      if (failure) rejectExecutionRecord(res, failure.status, failure.error, failure.message);
      else rejectExecutionRecord(res, 500, "Internal Server Error", "Failed to load execution record");
    }
  }));
}
