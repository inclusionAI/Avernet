import { createHash } from "node:crypto";
import { BotRuntimeError, type BotRuntime } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { SessionRecoveryError } from "./errors.js";
import type { DeliveryReceipt, DeliveryRecord, RecoveryActor, RecoveryDeliveryStore, RecoveryLogger, RecoveryResultReporter, SessionRecoveryApi } from "./contracts.js";
import { recoveryAdvice } from "./handbook.js";
import { parseRequest } from "./validation.js";
import { recoveryResult } from "./result.js";

// This deadline settles uncertainty only; it NEVER permits another send.
export const DELIVERY_SETTLEMENT_MS = 10 * 60 * 1000;
export class SessionRecoveryService implements SessionRecoveryApi {
  private readonly now: () => number;
  constructor(private readonly deps: {
    runtime: Pick<BotRuntime, "sendMessage">;
    store: RecoveryDeliveryStore;
    logger: RecoveryLogger;
    reporter: RecoveryResultReporter;
    now?: () => number;
  }) { this.now = deps.now ?? Date.now; }
  async create(raw: unknown, actor: RecoveryActor) {
    const request = parseRequest(raw);
    if (actor.userId !== request.target.entity_id && !actor.isAdmin) throw new SessionRecoveryError(403, "recovery_forbidden", "无权发起目标 Bot 自愈");
    // TE remains outside the verified MVP; never silently route it to OpenClaw.
    if (request.target.engine !== "OC") throw new SessionRecoveryError(422, "unsupported_engine", "当前会话自愈仅支持 OC");
    // parseRequest constructs a canonical field order, independent of the caller's JSON order.
    const fingerprint = createHash("sha256").update(JSON.stringify(request)).digest("hex");
    const startedAt = this.now();
    const claim = await this.deps.store.claim(request, fingerprint, startedAt, startedAt + DELIVERY_SETTLEMENT_MS);
    let record = claim.record;
    if (claim.acquired) {
      const advice = recoveryAdvice(request.tc_fault_label, request.delivery_key);
      let receipt: DeliveryReceipt;
      let outcome: NonNullable<DeliveryRecord["outcome"]> | undefined;
      try {
        receipt = await this.deps.runtime.sendMessage({
          target: { environment: request.target.env, ownerId: request.target.entity_id, botId: request.target.bot_id },
          expectedEngine: "openclaw", sessionKey: request.session_key, deliveryKey: request.delivery_key,
          message: advice.message, messageMarker: `[会话自愈：${request.delivery_key}]`,
        });
      } catch (error) {
        if (error instanceof BotRuntimeError && [403, 404, 409, 422].includes(error.status)) {
          // Runtime rejects invalid snapshots before sending. Preserve the rejection on retries.
          outcome = { rejection: { status: error.status === 403 ? 403 : 422, code: error.code, message: "目标信息无效或无权操作目标" } };
        }
        receipt = { status: "unknown" };
      }
      record = await this.deps.store.finish(request, outcome ?? { result: recoveryResult(request, receipt, startedAt, this.now()) });
    } else if (!record.outcome) {
      if (this.now() < record.settleAfter) throw new SessionRecoveryError(503, "recovery_in_progress", "该投递仍在处理中，请使用原 delivery_key 重试");
      // After a crash, we cannot know whether the channel accepted a message. Never resend.
      record = await this.deps.store.finish(request, { result: recoveryResult(request, { status: "unknown" }, record.startedAt, record.settleAfter) });
    }
    if (!record.outcome) throw new SessionRecoveryError(503, "recovery_store_unavailable", "投递结果未保存");
    if ("rejection" in record.outcome) {
      const error = record.outcome.rejection;
      throw new SessionRecoveryError(error.status, error.code, error.message);
    }
    const result = record.outcome.result;
    this.deps.logger.log({ event: "session_recovery_delivery", ...result });
    if (!record.callbackDelivered) {
      await this.deps.reporter.report(result);
      await this.deps.store.markReported(request);
    }
    return { status: "accepted" as const, event_id: request.event_id, delivery_key: request.delivery_key };
  }
}
