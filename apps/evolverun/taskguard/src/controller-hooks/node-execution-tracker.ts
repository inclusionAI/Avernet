/**
 * NodeExecutionTracker — inserts/updates node_executions rows
 * at node start, completion, and retry transitions.
 *
 * Single-path design: every node goes through a two-step lifecycle:
 *   INSERT(running) at node_started → UPDATE(completion) at node_succeeded/failed/rejected.
 *
 * The INSERT uses INSERT OR IGNORE so duplicate attempts (e.g. manual retries
 * re-using the same attempt number) are handled gracefully: the existing row
 * is simply UPDATEd with the final status.
 *
 * When the UPDATE is called with expectedVersion (from the INSERT result's row
 * version), optimistic locking protects against cross-process race conditions.
 *
 * A fallback INSERT handles the edge case where UPDATE matches 0 rows
 * (e.g. the INSERT was IGNORE'd and the existing row has a different version).
 */
import type { INodeExecutionRepository, NodeExecutionCompletion } from "../db/repositories/types.js";
import { isWarningsErrorText } from "../warnings.js";
import type {
  NodeLifecycleEvent,
  NodeLifecyclePayload,
} from "./types.js";

/**
 * Extract the warningsErrorText string from a NodeLifecyclePayload's
 * systemContext. The controller stores it under key "warningsErrorText"
 * when a node succeeds with warnings (e.g. embedded-agent tool errors).
 */
function extractWarningsErrorText(
  systemContext: Record<string, unknown> | null | undefined,
): string | null {
  if (!systemContext) return null;
  const val = systemContext["warningsErrorText"];
  if (typeof val === "string" && val.length > 0 && isWarningsErrorText(val)) {
    return val;
  }
  return null;
}

export class NodeExecutionTracker {
  /** Track in-flight inserts to avoid duplicates. */
  private inFlightInserts = new Set<string>();
  /** Pending insert promises so completions can wait for the row to exist. */
  private pendingInserts = new Map<string, Promise<void>>();

  constructor(
    private repo: INodeExecutionRepository,
    private maxIoBytes: number = 10 * 1024,
  ) {}

  private key(flowId: string, nodeId: string, attempt: number): string {
    return `${flowId}:${nodeId}:${attempt}`;
  }

  /**
   * Handle lifecycle events to track node execution state.
   */
  onEvent(event: NodeLifecycleEvent, payload: NodeLifecyclePayload): void {
    switch (event) {
      case "node_started":
        this.onNodeStart(payload);
        break;
      case "node_succeeded":
        console.log(`[tracker] onNodeComplete(succeeded) flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} executorType=${payload.executorType}`);
        void this.onNodeComplete(payload, "succeeded");
        break;
      case "node_failed":
        console.log(`[tracker] onNodeComplete(failed) flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} executorType=${payload.executorType}`);
        void this.onNodeComplete(payload, "failed");
        break;
      case "node_rejected":
        // Human reject (业务驳回) — record as "rejected", distinct from "failed"
        // so it neither inflates failed_count nor triggers failure alert paths.
        console.log(`[tracker] onNodeComplete(rejected) flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} executorType=${payload.executorType}`);
        void this.onNodeComplete(payload, "rejected");
        break;
      case "node_retry":
        void this.onNodeRetry(payload);
        break;
      case "node_skipped":
        this.onNodeSkipped(payload);
        break;
      case "node_progress":
        this.onNodeProgress(payload);
        break;
    }
  }

  private onNodeStart(payload: NodeLifecyclePayload): void {
    const k = this.key(payload.flowId, payload.nodeId, payload.attempt);

    // Avoid duplicate inserts for the same node+attempt
    if (this.inFlightInserts.has(k)) return;
    this.inFlightInserts.add(k);

    if (payload.embeddedSessionKey) {
      console.log(`[tracker] onNodeStart embeddedSessionKey flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} executorType=${payload.executorType} embeddedSessionKey=${payload.embeddedSessionKey}`);
    }

    // INSERT a "running" row.
    // INSERT OR IGNORE ensures that if a previous row exists (e.g. from a
    // prior failed attempt that was manually retried with the same attempt
    // number), we don't create a duplicate. The existing row will be
    // UPDATEd by onNodeComplete.
    const insertPromise = this.repo
      .insert({
        flowId: payload.flowId,
        workflowId: payload.workflowId,
        nodeId: payload.nodeId,
        executorType: payload.executorType,
        status: "running",
        attempt: payload.attempt,
        nodeTitle: payload.nodeTitle ?? null,
        progressMessage: payload.progressMessage ?? null,
        inputJson: payload.inputJson ?? null,
        sessionKey: payload.sessionKey ?? null,
        sessionId: payload.sessionId ?? null,
        embeddedSessionKey: payload.embeddedSessionKey ?? null,
        systemContextJson: payload.systemContext ? JSON.stringify(payload.systemContext) : null,
        resolvedPrompt: payload.resolvedPrompt ?? null,
        startedAt: Math.floor(Date.now() / 1000),
      })
      .then(async (insertResult) => {
        this.inFlightInserts.delete(k);
        // If INSERT OR IGNORE skipped (affectedRows = 0), the row already
        // exists from a prior attempt (e.g. manual retry resetting attempts
        // to 0 → re-running at attempt=1). Reset it to "running" with a fresh
        // started_at so the UI and subsequent UPDATEs work correctly.
        //
        // We AWAIT this reset (not fire-and-forget) so the pendingInserts
        // promise below does not resolve until the reset has committed.
        // onNodeComplete awaits the same promise before issuing its
        // completion UPDATE — without awaiting here, the two UPDATEs race
        // and the reset (completed_at=0, status=running) can land AFTER the
        // completion UPDATE, leaving the retried row stuck in "running" with
        // a stale/zero completed_at.
        if (insertResult.affectedRows === 0) {
          await this.repo.updateCompletionByFlowNode(
            payload.flowId,
            payload.nodeId,
            payload.attempt,
            {
              status: "running",
              outputJson: null,
              durationMs: null,
              tokenUsageJson: null,
              embeddedSessionKey: payload.embeddedSessionKey ?? null,
              systemContextJson: payload.systemContext ? JSON.stringify(payload.systemContext) : null,
              resolvedPrompt: payload.resolvedPrompt ?? null,
              completedAt: 0,
              startedAt: Math.floor(Date.now() / 1000),
            },
          ).catch((err) => {
            const errMsg = err instanceof Error ? err.message : String(err);
            console.warn(`[tracker] updateCompletionByFlowNode (reset) failed: flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} error=${errMsg}`);
          });
        }
      })
      .catch((e) => {
        this.inFlightInserts.delete(k);
        const msg = e instanceof Error ? e.message : String(e);
        console.warn(`[tracker] onNodeStart insert failed: flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} error=${msg}`);
      })
      .finally(() => {
        this.pendingInserts.delete(k);
      });

    this.pendingInserts.set(k, insertPromise);
  }

  private async onNodeComplete(payload: NodeLifecyclePayload, status: string): Promise<void> {
    const k = this.key(payload.flowId, payload.nodeId, payload.attempt);

    const completion: NodeExecutionCompletion = {
      status,
      outputJson: payload.outputJson ?? null,
      durationMs: payload.durationMs ?? null,
      tokenUsageJson: payload.usage ? JSON.stringify(payload.usage) : null,
      embeddedSessionKey: payload.embeddedSessionKey ?? null,
      systemContextJson: payload.systemContext ? JSON.stringify(payload.systemContext) : null,
      resolvedPrompt: payload.resolvedPrompt ?? null,
      completedAt: Math.floor(Date.now() / 1000),
    };

    if (status === "failed") {
      completion.errorText = payload.error ?? null;
    } else if (status === "rejected") {
      completion.errorText = payload.error ?? null;
    } else if (status === "succeeded") {
      const warningsErrorText = extractWarningsErrorText(payload.systemContext);
      if (warningsErrorText) {
        completion.errorText = warningsErrorText;
      }
    }

    // Wait for the onNodeStart INSERT to commit before updating.
    // This ensures the row exists so the subsequent UPDATE has something to match.
    const pending = this.pendingInserts.get(k);
    if (pending) {
      await pending.catch((err) => {
        const errMsg = err instanceof Error ? err.message : String(err);
        console.warn(`[tracker] pending insert wait failed (onNodeComplete): flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} error=${errMsg}`);
      });
    }

    // Try UPDATE with optimistic locking — normal path when INSERT has committed.
    try {
      const updated = await this.repo.updateCompletionByFlowNode(
        payload.flowId,
        payload.nodeId,
        payload.attempt,
        completion,
      );

      if (updated) {
        console.log(`[tracker] updateCompletionByFlowNode OK flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} status=${status}`);
        return;
      }

      // UPDATE matched 0 rows — possible when:
      //   a) INSERT was IGNORE'd (row existed from prior attempt) and version
      //      was incremented by a concurrent UPDATE, making the expectedVersion stale.
      //   b) INSERT failed entirely.
      // In both cases, fall through to INSERT a completion row directly.
      console.warn(`[tracker] updateCompletionByFlowNode matched 0 rows, falling back to INSERT: flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} status=${status}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.warn(`[tracker] updateCompletionByFlowNode threw, falling back to INSERT: flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} status=${status} error=${msg}`);
    }

    // Fallback INSERT: create a row with the final status directly.
    // This handles the edge case where the UPDATE matched 0 rows.
    try {
      const startedAt = Math.floor(Date.now() / 1000) - Math.floor((completion.durationMs ?? 0) / 1000);
      await this.repo.insert({
        flowId: payload.flowId,
        workflowId: payload.workflowId,
        nodeId: payload.nodeId,
        executorType: payload.executorType,
        status,
        attempt: payload.attempt,
        nodeTitle: payload.nodeTitle ?? null,
        progressMessage: payload.progressMessage ?? null,
        inputJson: payload.inputJson ?? null,
        outputJson: completion.outputJson,
        errorText: completion.errorText,
        durationMs: completion.durationMs,
        tokenUsageJson: completion.tokenUsageJson,
        sessionKey: payload.sessionKey ?? null,
        sessionId: payload.sessionId ?? null,
        embeddedSessionKey: payload.embeddedSessionKey ?? null,
        systemContextJson: completion.systemContextJson,
        resolvedPrompt: payload.resolvedPrompt ?? completion.resolvedPrompt ?? null,
        startedAt,
        completedAt: completion.completedAt,
      });
      console.log(`[tracker] fallback INSERT OK flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} status=${status}`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.warn(`[tracker] fallback INSERT FAILED flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} status=${status} error=${msg}`);
    } finally {
      this.inFlightInserts.delete(k);
    }
  }

  private async onNodeRetry(payload: NodeLifecyclePayload): Promise<void> {
    const failedAttempt = payload.attempt;
    const k = this.key(payload.flowId, payload.nodeId, failedAttempt);

    // Wait for the failed attempt's row to exist before updating it.
    const pending = this.pendingInserts.get(k);
    if (pending) {
      await pending.catch((err) => {
        const errMsg = err instanceof Error ? err.message : String(err);
        console.warn(`[tracker] pending insert wait failed (onNodeRetry): flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${failedAttempt} error=${errMsg}`);
      });
    }

    try {
      const result = await this.repo.updateCompletionByFlowNode(
        payload.flowId,
        payload.nodeId,
        failedAttempt,
        {
          status: "failed",
          outputJson: payload.outputJson ?? null,
          errorText: payload.error ?? null,
          durationMs: payload.durationMs ?? null,
          systemContextJson: payload.systemContext ? JSON.stringify(payload.systemContext) : null,
          completedAt: Math.floor(Date.now() / 1000),
        },
      );
      if (!result) {
        console.warn(`[tracker] onNodeRetry updateCompletion returned false: flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${failedAttempt}`);
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      console.warn(`[tracker] onNodeRetry updateCompletion failed: flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${failedAttempt} error=${msg}`);
    }

    // NOTE: We do NOT insert a row for the next attempt here.
    // The controller will emit node_started for the next attempt,
    // which triggers onNodeStart to INSERT that row.
  }

  private onNodeSkipped(payload: NodeLifecyclePayload): void {
    const k = this.key(payload.flowId, payload.nodeId, payload.attempt);

    if (this.inFlightInserts.has(k)) return;
    this.inFlightInserts.add(k);

    const now = Math.floor(Date.now() / 1000);
    void this.repo
      .insert({
        flowId: payload.flowId,
        workflowId: payload.workflowId,
        nodeId: payload.nodeId,
        executorType: payload.executorType,
        status: "skipped",
        attempt: payload.attempt,
        nodeTitle: payload.nodeTitle ?? null,
        errorText: payload.error ?? null,
        sessionKey: payload.sessionKey ?? null,
        sessionId: payload.sessionId ?? null,
        embeddedSessionKey: payload.embeddedSessionKey ?? null,
        systemContextJson: payload.systemContext ? JSON.stringify(payload.systemContext) : null,
        startedAt: now,
        completedAt: now,
      })
      .then(() => {
        this.inFlightInserts.delete(k);
      })
      .catch((e) => {
        this.inFlightInserts.delete(k);
        const msg = e instanceof Error ? e.message : String(e);
        console.warn(`[tracker] onNodeSkipped insert failed: flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} error=${msg}`);
      });
  }

  private onNodeProgress(payload: NodeLifecyclePayload): void {
    if (!payload.progressMessage) return;
    void this.repo
      .updateProgressMessage(payload.flowId, payload.nodeId, payload.attempt, payload.progressMessage)
      .catch((e) => {
        const msg = e instanceof Error ? e.message : String(e);
        console.warn(`[tracker] onNodeProgress updateProgressMessage failed: flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} error=${msg}`);
      });
  }
}