import { createHash } from "node:crypto";
import { logWorkflowBackground, workflowBackgroundError } from "@avernet/clawweb-shared/server/services/workflow-background-log";
import type { SessionRecoveryApi } from "../repair/session-recovery/contracts.js";
import { SessionRecoveryError } from "../repair/session-recovery/errors.js";
import { parseRequest } from "../repair/session-recovery/validation.js";
import {
  DIAGNOSIS_V3_VERSION, DIAGNOSIS_V4_VERSION, MonitoringError, type DiagnosisEvent, type MonitoringRecoveryConfigStore, type MonitoringRecoveryTrigger, type MonitoringTarget,
} from "./contracts.js";
import { ACTION_POLICY_VERSION, recoverySkipReason } from "./action-policy.js";
import { target, targetKey } from "./target.js";

export const SESSION_RECOVERY_ALLOWLIST_KEY = "session_recovery_allowlist";

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseAllowlist(value: unknown): MonitoringTarget[] {
  if (!record(value) || Object.keys(value).some(key => key !== "allowed_bots") || !Array.isArray(value.allowed_bots)) {
    throw new Error("Invalid recovery allowlist");
  }
  const seen = new Set<string>();
  return value.allowed_bots.map(item => {
    if (!record(item) || Object.keys(item).length !== 3
      || Object.keys(item).some(key => !["bot_id", "entity_id", "env"].includes(key))
      || typeof item.bot_id !== "string" || !/^[A-Za-z0-9_-]+$/.test(item.bot_id)
      || typeof item.entity_id !== "string" || !/^[A-Za-z0-9_.@+-]+$/.test(item.entity_id) || item.entity_id.includes("..")
      || (item.env !== "pre" && item.env !== "prod")) throw new Error("Invalid recovery target");
    const entry = target({ botId: item.bot_id, entityId: item.entity_id, env: item.env });
    const key = targetKey(entry);
    if (seen.has(key)) throw new Error("Duplicate recovery target");
    seen.add(key);
    return entry;
  });
}

/** Caller-side mapping only; semantic/device validation remains owned by recovery. */
export function buildRecoveryTarget(event: DiagnosisEvent) {
  const original = { bot_id: event.botId, entity_id: event.entityId, env: event.env, engine: event.engine };
  if ((event.schemaVersion !== DIAGNOSIS_V3_VERSION && event.schemaVersion !== DIAGNOSIS_V4_VERSION) || event.engine !== "OC") return original;
  const identity = event.runtimeIdentity;
  if (!identity || !identity.BOT_ID || !identity.OWNER_ID || identity.BOT_ID !== event.botId) return null;
  return { ...original, bot_id: identity.BOT_ID, entity_id: identity.OWNER_ID,
    runtime_identity: identity };
}

export function createMonitoringRecoveryTrigger(input: {
  config: MonitoringRecoveryConfigStore;
  service: SessionRecoveryApi | null;
}): MonitoringRecoveryTrigger {
  return {
    async trigger(event) {
      const reason = recoverySkipReason(event);
      if (reason) {
        console.info("[monitoring] recovery skipped", { eventId: event.eventId, policy: ACTION_POLICY_VERSION, reason });
        return;
      }
      let allowed: MonitoringTarget[];
      try {
        const row = await input.config.findByKey(SESSION_RECOVERY_ALLOWLIST_KEY);
        if (!row || row.enabled === 0) return;
        if (row.enabled !== 1) throw new Error("Invalid recovery configuration state");
        allowed = parseAllowlist(JSON.parse(row.config_json));
      } catch (error) {
        console.error("[monitoring] recovery unavailable", { eventId: event.eventId, reason: "allowlist_unavailable" });
        logWorkflowBackground("session_recovery_trigger", "configuration_unavailable", { eventId: event.eventId,
          botId: event.botId, targetEnvironment: event.env, ...workflowBackgroundError(error) });
        throw new MonitoringError("NOT_READY", "自愈内测白名单暂不可用或格式无效。");
      }
      if (!allowed.some(entry => targetKey(entry) === targetKey(event))) return;
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
        request = parseRequest({
          event_id: event.eventId, tc_fault_label: event.tcFaultLabel,
          target: recoveryTarget,
          session_key: event.sessionKey,
          ...(event.sessionId ? { session_id: event.sessionId } : {}),
          delivery_key: `monitoring-${createHash("sha256").update(event.eventId).digest("hex")}`,
          diagnosis,
        });
      } catch (error) {
        if (!(error instanceof SessionRecoveryError) || error.status !== 422) throw error;
        console.warn("[monitoring] recovery skipped", { eventId: event.eventId, reason: "invalid_recovery_input" });
        return;
      }
      if (!input.service) {
        logWorkflowBackground("session_recovery_task", "CREATE_FAILED", { eventId: event.eventId,
          botId: event.botId, targetEnvironment: event.env, phase: "create", outcome: "failed", count: 1, errorCode: "recovery_not_ready" });
        throw new MonitoringError("NOT_READY", "自愈服务尚未就绪。");
      }
      try {
        await input.service.create(request, { userId: event.entityId, isAdmin: false });
      } catch (error) {
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
