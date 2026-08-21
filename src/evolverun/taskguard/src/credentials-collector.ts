import * as fs from "node:fs";
import { getCredentialsPath, parseKeyValueFile } from "./credentials.js";
import { resolveSessionId } from "./session-resolver.js";

/**
 * Collected context for human intervention support.
 * Populated at workflow start time and persisted to flow_runs.
 */
export type CollectedContext = {
  /** Parsed .credentials content as JSON string (e.g. '{"real_bot_id":"default","staff_no":"151614"}'). */
  credentialsJson: string | null;
  /** sessionKey at workflow start time (e.g. "agent:main:dashboard:xxx-yyy"). */
  originSessionKey: string;
  /** Resolved session UUID from sessionKey. */
  originSessionId: string | null;
  /** BaaS-format bot_id "real_bot_id:staff_no" (e.g. "default:151614").
   *  real_bot_id: 个人 Bot 在"我的 Bot"页的 ID，或服务 Bot 在"我的服务 Bot"页的 ID。
   *  staff_no: Bot owner 工号。
   *  NOTE: Different from ClawMind's loadInstanceId() which uses "staff_no_real_bot_id" (underscore, reversed order). */
  originBotId: string | null;
};

/**
 * Collect credentials and session info for human intervention.
 *
 * Reads the .credentials file for real_bot_id/staff_no (to build the BaaS bot_id),
 * and resolves the sessionKey to a sessionId.
 *
 * IAM_TOKEN is NOT stored here — it comes from application.yaml's baas_token config.
 *
 * All failures are non-fatal: missing .credentials does not prevent workflow execution.
 */
export function collectCredentialsAndSession(sessionKey: string, sessionId?: string): CollectedContext {
  let credentialsJson: string | null = null;
  let originBotId: string | null = null;

  const credentialsPath = getCredentialsPath();
  try {
    if (fs.existsSync(credentialsPath)) {
      const content = fs.readFileSync(credentialsPath, "utf-8");
      const parsed = parseKeyValueFile(content);
      credentialsJson = JSON.stringify(parsed);

      // BaaS bot_id format: "real_bot_id:staff_no"
      // e.g. BOT_ID=default, OWNER_ID=151614 → "default:151614"
      const botId = parsed.BOT_ID;
      const ownerId = parsed.OWNER_ID;
      if (botId && ownerId) {
        originBotId = `${botId}:${ownerId}`;
      }
    }
  } catch {
    // Non-fatal: credentials not available
  }

  const originSessionId = sessionId ?? resolveSessionId(sessionKey);

  return {
    credentialsJson,
    originSessionKey: sessionKey,
    originSessionId,
    originBotId,
  };
}