import { createHash } from "node:crypto";
import { BotRuntimeError, type BotRuntime, type BotTargetResolver, type ResolvedBotTarget } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryError } from "./errors.js";
import type { DeliveryReceipt, DeliveryRecord, RecoveryActor, RecoveryDeliveryStore, RecoveryLogger, RecoveryRequest, RecoveryResultReporter, SessionRecoveryApi } from "./contracts.js";
import { recoveryAdvice } from "./handbook.js";
import { parseRequest } from "./validation.js";
import { recoveryResult } from "./result.js";

// This deadline settles uncertainty only; it NEVER permits another send.
export const DELIVERY_SETTLEMENT_MS = 10 * 60 * 1000;
export class SessionRecoveryService implements SessionRecoveryApi {
  private readonly now: () => number;
  constructor(private readonly deps: {
    runtime: Pick<BotRuntime, "sendMessage">;
    targets: BotTargetResolver;
    store: RecoveryDeliveryStore;
    logger: RecoveryLogger;
    reporter: RecoveryResultReporter;
    now?: () => number;
  }) { this.now = deps.now ?? Date.now; }

  async create(raw: unknown, actor: RecoveryActor) {
    const request = parseRequest(raw);
    if (actor.userId !== request.target.entity_id && !actor.isAdmin) throw new SessionRecoveryError(403, "recovery_forbidden", "无权发起目标 Bot 自愈");
    if (request.target.engine !== "OC") throw new SessionRecoveryError(422, "unsupported_engine", "当前会话自愈仅支持 OC");
    const fingerprint = createHash("sha256").update(JSON.stringify(request)).digest("hex");
    const existing = await this.deps.store.find(request);
    if (existing && existing.fingerprint !== fingerprint) throw new SessionRecoveryError(409, "session_recovery_conflict", "相同 event_id 和 delivery_key 的请求内容不一致");
    let record = existing;
    if (!existing) {
      // Resolve the target synchronously so invalid snapshots still receive 403/422.
      let target: ResolvedBotTarget;
      try {
        target = await this.deps.targets.resolve({ environment: request.target.env, ownerId: request.target.entity_id, botId: request.target.bot_id });
        if (target.environment !== request.target.env || target.ownerId !== request.target.entity_id || target.botId !== request.target.bot_id || target.activeEngine !== "openclaw") {
          throw new BotRuntimeError(422, "invalid_recovery_target", "目标信息无效或引擎不匹配");
        }
      } catch (error) {
        if (error instanceof BotRuntimeError && [403, 404, 409, 422].includes(error.status)) {
          const rejection = { status: error.status === 403 ? 403 : 422, code: error.code, message: "目标信息无效或无权操作目标" };
          throw new SessionRecoveryError(rejection.status, rejection.code, rejection.message);
        }
        throw error;
      }
      // Only a successful preflight may consume the send key. A transient lookup failure stays retryable.
      const startedAt = this.now();
      const claim = await this.deps.store.claim(request, fingerprint, startedAt, startedAt + DELIVERY_SETTLEMENT_MS);
      record = claim.record;
      if (claim.acquired) {
        this.schedule(request, () => this.deliver(request, target, startedAt));
        return this.acknowledgment(request);
      }
    }
    if (!record) throw new SessionRecoveryError(503, "recovery_store_unavailable", "投递记录暂不可用");
    if (record.outcome && "rejection" in record.outcome) {
      const error = record.outcome.rejection;
      throw new SessionRecoveryError(error.status, error.code, error.message);
    } else if (record.outcome) {
      const completed = record;
      this.schedule(request, () => this.report(request, completed));
    } else if (this.now() >= record.settleAfter) {
      // After a crash, a repeated request may settle uncertainty, but never resend.
      const settled = await this.deps.store.finish(request, { result: recoveryResult(request, { status: "unknown" }, record.startedAt, record.settleAfter) });
      this.schedule(request, () => this.report(request, settled));
    }
    // Pending duplicates are also accepted. Neither sends nor callbacks delay this acknowledgment.
    return this.acknowledgment(request);
  }

  private acknowledgment(request: RecoveryRequest) {
    return { status: "accepted" as const, event_id: request.event_id, delivery_key: request.delivery_key };
  }

  private schedule(request: RecoveryRequest, run: () => Promise<void>): void {
    setImmediate(() => {
      void run().catch(() => {
        // The durable claim/result remains available for an identical request to retry the callback.
        // Never log raw transport or persistence exceptions, which can include credentials.
        console.error("[session-recovery] background processing failed", JSON.stringify({ event_id: request.event_id, delivery_key: request.delivery_key }));
      });
    });
  }

  private async deliver(request: RecoveryRequest, target: ResolvedBotTarget, startedAt: number): Promise<void> {
    const advice = recoveryAdvice(request.tc_fault_label, request.delivery_key);
    let receipt: DeliveryReceipt;
    try {
      receipt = await this.deps.runtime.sendMessage({ target, expectedEngine: "openclaw", sessionKey: request.session_key,
        deliveryKey: request.delivery_key, message: advice.message, messageMarker: `[会话自愈：${request.delivery_key}]` });
    } catch (error) {
      receipt = { status: error instanceof BotRuntimeError && [403, 404, 409, 422].includes(error.status) ? "failed" : "unknown" };
    }
    const record = await this.deps.store.finish(request, { result: recoveryResult(request, receipt, startedAt, this.now()) });
    await this.report(request, record);
  }

  private async report(request: RecoveryRequest, record: DeliveryRecord): Promise<void> {
    if (!record.outcome || !("result" in record.outcome)) return;
    const result = record.outcome.result;
    this.deps.logger.log({ event: "session_recovery_delivery", ...result });
    if (!record.callbackDelivered) {
      await this.deps.reporter.report(result);
      await this.deps.store.markReported(request);
    }
  }
}
