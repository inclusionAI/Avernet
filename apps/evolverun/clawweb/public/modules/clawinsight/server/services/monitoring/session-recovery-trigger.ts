import { createHash } from "node:crypto";
import { logRepairEvent } from "../repair/common/logging.js";
import type { SessionRecoveryApi } from "../repair/session-recovery/contracts.js";
import { SessionRecoveryError } from "../repair/session-recovery/errors.js";
import { isRecoveryTraceId, parseRequest } from "../repair/session-recovery/validation.js";
import {
  DIAGNOSIS_V3_VERSION, DIAGNOSIS_V4_VERSION, MonitoringError, type DiagnosisEvent, type MonitoringRecoveryTrigger, type RuntimeIdentity,
} from "./contracts.js";
import { RECOVERY_POLICY_VERSION, recoverySkipReason } from "./action-policy.js";
import type { RecoveryAppConfigStore } from "../repair/session-recovery/configuration-contracts.js";
import type { BotDiagnosisRecoveryConfigStore } from './bot-config-contracts.js';


function recoveryRuntimeIdentity(event: DiagnosisEvent, identity: RuntimeIdentity): RuntimeIdentity {
  if (identity.BOT_TYPE !== "service" || identity.STAGE !== "online" || !event.sessionNasPath) return identity;
  const parts = event.sessionNasPath.split("/");
  const storage = [event.env, identity.ENTITY_TYPE, identity.ENTITY_ID, "openclaw", event.botId].join("_");
  const agent = /^agent:([A-Za-z0-9_-]+):/.exec(event.sessionKey ?? "")?.[1];
  const namespace = parts.at(-6) ?? "", filename = parts.at(-1) ?? "";
  const deviceUuid = namespace.startsWith(storage + "_") ? namespace.slice(storage.length + 1) : "";
  // Parse only this Bot's exact session namespace. Recovery still validates the device against its binding.
  if (!identity.ENTITY_TYPE || !identity.ENTITY_ID || parts.at(-7) !== event.env
    || !/^DEVICE-[a-f0-9]{32}$/.test(deviceUuid) || parts.at(-5) !== ".openclaw" || parts.at(-4) !== "agents"
    || !agent || parts.at(-3) !== agent || parts.at(-2) !== "sessions" || !/^[A-Za-z0-9_-]+\.jsonl$/.test(filename)
    || event.sessionId && filename !== event.sessionId + ".jsonl") return identity;
  if (identity.CLIENT_ID === deviceUuid) return identity;
  console.warn("[monitoring] recovery instance corrected", { eventId: event.eventId, botId: event.botId,
    previousClientId: /^DEVICE-[a-f0-9]{32}$/.test(identity.CLIENT_ID ?? "") ? identity.CLIENT_ID : "invalid", deviceUuid });
  return { ...identity, CLIENT_ID: deviceUuid };
}

/** Caller-side mapping only; semantic/device validation remains owned by recovery. */
export function buildRecoveryTarget(event: DiagnosisEvent) {
  const original = { bot_id: event.botId, entity_id: event.entityId, env: event.env, engine: event.engine };
  if ((event.schemaVersion !== DIAGNOSIS_V3_VERSION && event.schemaVersion !== DIAGNOSIS_V4_VERSION) || event.engine !== "OC") return original;
  const identity = event.runtimeIdentity;
  if (!identity || !identity.BOT_ID || !identity.OWNER_ID || identity.BOT_ID !== event.botId) return null;
  return { ...original, bot_id: identity.BOT_ID, entity_id: identity.OWNER_ID,
    runtime_identity: recoveryRuntimeIdentity(event, identity) };
}

export function createMonitoringRecoveryTrigger(input: {
  /** @deprecated Admission is enforced by the recovery service; retained for existing hosts. */
  config?: Pick<BotDiagnosisRecoveryConfigStore, 'find'> | RecoveryAppConfigStore;
  service: SessionRecoveryApi | null;
}): MonitoringRecoveryTrigger {
  return {
    async trigger(event) {
      const reason = recoverySkipReason(event);
      if (reason) {
        console.info("[monitoring] recovery skipped", { eventId: event.eventId, policy: RECOVERY_POLICY_VERSION, reason });
        return;
      }
      const recoveryTarget = buildRecoveryTarget(event);
      if (recoveryTarget === null) {
        console.warn("[monitoring] recovery skipped", { eventId: event.eventId, reason: "runtime_identity_unavailable_or_mismatched" });
        return;
      }
      let request;
      try {
        const diagnosis = [
          event.businessDiagnosis?.trim() ? `业务诊断：${event.businessDiagnosis}` : "",
          event.systemDiagnosis?.trim() ? `系统诊断：${event.systemDiagnosis}` : "",
        ].filter(Boolean).join("\n");
        const faultAtMs = event.occurredAt ? Date.parse(event.occurredAt) : NaN;
        request = parseRequest({
          event_id: event.eventId, tc_fault_label: event.tcFaultLabel,
          target: recoveryTarget,
          session_key: event.sessionKey,
          ...(event.sessionId ? { session_id: event.sessionId } : {}),
          ...(isRecoveryTraceId(event.traceId) ? { trace_id: event.traceId } : {}),
          ...(Number.isSafeInteger(faultAtMs) && faultAtMs > 0 ? { fault_at_ms: faultAtMs } : {}),
          delivery_key: `monitoring-${createHash("sha256").update(event.eventId).digest("hex")}`,
          diagnosis,
        });
      } catch (error) {
        if (!(error instanceof SessionRecoveryError) || error.status !== 422) throw error;
        console.warn("[monitoring] recovery skipped", { eventId: event.eventId, reason: "invalid_recovery_input" });
        return;
      }
      if (!input.service) {
        logRepairEvent("session-recovery", "session_recovery_task", "CREATE_FAILED", { eventId: event.eventId,
          botId: event.botId, targetEnvironment: event.env, phase: "create", outcome: "failed", count: 1, errorCode: "recovery_not_ready" });
        throw new MonitoringError("NOT_READY", "自愈服务尚未就绪。");
      }
      try {
        await input.service.create(request, { userId: event.entityId, isAdmin: false });
      } catch (error) {
        if (error instanceof SessionRecoveryError && error.code === "recovery_disabled") {
          console.info("[monitoring] recovery skipped", { eventId: event.eventId, reason: "recovery_disabled" });
          return;
        }
        console.error("[monitoring] recovery creation failed", { eventId: event.eventId,
          code: error instanceof SessionRecoveryError ? error.code : "recovery_unavailable" });
        if (error instanceof SessionRecoveryError && error.status < 500) {
          throw new MonitoringError(error.status === 409 ? "EVENT_CONFLICT" : "INVALID_EVENT", "自愈创建请求被拒绝，诊断记录已保存。");
        }
        throw new MonitoringError("NOT_READY", "诊断记录已保存，自愈任务尚未确认创建，请重试上报。");
      }
    },
  };
}
