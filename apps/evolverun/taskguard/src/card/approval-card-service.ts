/**
 * DingTalk interactive approval card service.
 *
 * Creates and delivers approval cards via the DingTalk Card API
 * (createAndDeliver) and updates card variables after approval actions
 * (updateCardVariables).
 *
 * Uses template: e4b47b7b-5b19-4712-8499-3e80a2c10fa7.schema
 * (ClawFlow Approval — published v1.0.4)
 */

import { readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// ── ConversationId case resolution ──────────────────────────────────────

/**
 * DingTalk openConversationId values are base64-encoded and case-sensitive.
 * OpenClaw normalizes session keys to lowercase, so bcsGroupId extracted
 * from session keys has lost the original casing. The DingTalk Card API
 * enforces case-sensitive openSpaceId — a lowercased conversationId results
 * in the card being silently accepted but never appearing in the chat.
 *
 * This function reads the original-cased conversationId from OpenClaw
 * session store files, using the same approach as the DingTalk connector
 * peer-id-registry module.
 *
 * Returns the original-cased ID if found, otherwise returns the input as-is.
 */
const peerIdCache = new Map<string, string>();
let peerIdCacheLoaded = false;

export function resolveOriginalConversationId(id: string): string {
  if (!id || !id.startsWith("cid")) return id; // Only DingTalk group IDs need case restoration

  if (!peerIdCacheLoaded) {
    loadPeerIdsFromSessions();
    peerIdCacheLoaded = true;
  }

  return peerIdCache.get(id.toLowerCase()) ?? id;
}

function loadPeerIdsFromSessions(): void {
  const home = homedir();
  const agentsDir = join(home, ".openclaw", "agents");

  try {
    const agentDirs = readdirSync(agentsDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);

    for (const agentName of agentDirs) {
      const sessionsPath = join(agentsDir, agentName, "sessions", "sessions.json");
      try {
        const raw = readFileSync(sessionsPath, "utf-8");
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object") continue;

        for (const session of Object.values(parsed as Record<string, unknown>)) {
          if (!session || typeof session !== "object") continue;
          const record = session as Record<string, unknown>;

          // Register original-cased IDs from known fields
          registerCandidatePeerId(record.lastTo);
          if (record.origin && typeof record.origin === "object") {
            const origin = record.origin as Record<string, unknown>;
            registerCandidatePeerId(origin.from);
            registerCandidatePeerId(origin.to);
          }
        }
      } catch {
        // sessions.json may not exist for some agents; skip silently
      }
    }
  } catch {
    // agents dir may not exist; skip silently
  }
}

function registerCandidatePeerId(value: unknown): void {
  if (typeof value === "string" && value.startsWith("cid")) {
    peerIdCache.set(value.toLowerCase(), value);
  }
}

// ── Types ──────────────────────────────────────────────────────────────

/** Parameters for creating and delivering an approval card. */
export type CreateApprovalCardParams = {
  /** Approval title (e.g., "Q3营销方案审批") */
  title: string;
  /** Applicant name */
  applicant: string;
  /** Workflow title */
  workflowTitle: string;
  /** Approval detail content as plain text. Includes fields + applicant + approver info. BaseText does not render Markdown. */
  content: string;
  /** Comma-separated approver names for display */
  approverList: string;
  /** URL to the workflow detail page */
  workflowDetailUrl: string;
  /** Delivery target: private DM or group chat */
  deliveryMode: "private" | "dingtalk-group";
  /** openConversationId (for group) or userId (for private) */
  conversationId: string;
};

/** Result from createAndDeliver API call. */
export type ApprovalCardResult = {
  ok: boolean;
  outTrackId?: string;
  cardInstanceId?: string;
  error?: string;
};

// ── Constants ──────────────────────────────────────────────────────────

/**
 * ClawFlow Approval interactive card template.
 *
 * Published v1.0.4 (e4b47b7b) with fixes:
 * - StdCard: cardName=${title}, tagText=${statusLabel}
 * - CardHeaderV2: title=${workflowTitle}
 * - BaseText: content=${content}, maxLine=0 (unlimited)
 * - ButtonGroup: fixed buttons (同意/approve, 驳回/reject), visible=fixed true
 * - BaseText: content=${resultText}, maxLine=0, visible=isNotEmpty(resultText)
 *
 * v1.0.4 changes from v1.0.3:
 * - Content BaseText maxLine: 2 → 0 (show all content without truncation)
 * - ButtonGroup visible: condition-based (isTrue approveAction) → fixed true
 *   (editor preview doesn't render ButtonGroup, but actual delivery works correctly)
 * - ButtonGroup dynamicButtons: removed (was conflicting with buttonsSource:"fixed")
 * - ResultText BaseText maxLine: 2 → 0
 * - ResultText BaseText visible: always → isNotEmpty(resultText)
 * - Content variable: plain text (BaseText does NOT render Markdown)
 *
 * The cardParamMap supplies all variable values via createAndDeliver API.
 */
const APPROVAL_CARD_TEMPLATE_ID = "e4b47b7b-5b19-4712-8499-3e80a2c10fa7.schema";

const DINGTALK_CARD_API = "https://api.dingtalk.com/v1.0/card/instances";

// ── Card variable builder ──────────────────────────────────────────────

/**
 * Build the cardParamMap for the DingTalk approval card template.
 * Variable names must match the template's defined variables exactly.
 */
export function buildApprovalCardVariables(
  params: CreateApprovalCardParams,
): Record<string, string> {
  return {
    title: params.title,
    status: "waiting",
    statusLabel: "⏳ 待审批",
    applicant: params.applicant,
    workflowTitle: params.workflowTitle,
    content: params.content,
    approverList: params.approverList,
    approveAction: "true",
    rejectAction: "true",
    resultText: "",
    workflowDetailUrl: params.workflowDetailUrl,
  };
}

// ── Delivery helpers ───────────────────────────────────────────────────

/**
 * Build the openSpaceId for DingTalk card delivery.
 * - Group: dtv1.card//IM_GROUP.{conversationId}
 * - Private: dtv1.card//IM_ROBOT.{userId}
 */
export function buildOpenSpaceId(
  deliveryMode: "private" | "dingtalk-group",
  conversationId: string,
): string {
  return deliveryMode === "dingtalk-group"
    ? "dtv1.card//IM_GROUP." + conversationId
    : "dtv1.card//IM_ROBOT." + conversationId;
}

// ── createAndDeliver API ───────────────────────────────────────────────

/**
 * Create and deliver an interactive approval card via DingTalk Card API.
 * Uses POST /v1.0/card/instances/createAndDeliver with the ClawFlow
 * Approval template. The card is delivered to a group or private chat
 * and supports STREAM callbacks for button interactions.
 */
export async function createAndDeliverApprovalCard(
  token: string,
  robotCode: string,
  params: CreateApprovalCardParams,
): Promise<ApprovalCardResult> {
  const outTrackId = "approval_" + Date.now() + "_" + Math.random().toString(36).slice(2, 8);
  const isGroup = params.deliveryMode === "dingtalk-group";

  // Resolve original case-sensitive conversationId.
  // The bcsGroupId from session keys is lowercased, but DingTalk Card API
  // requires the original-cased openConversationId in openSpaceId.
  const resolvedConversationId = isGroup
    ? resolveOriginalConversationId(params.conversationId)
    : params.conversationId;

  const openSpaceId = buildOpenSpaceId(params.deliveryMode, resolvedConversationId);

  const body = {
    cardTemplateId: APPROVAL_CARD_TEMPLATE_ID,
    outTrackId,
    cardData: {
      cardParamMap: buildApprovalCardVariables(params),
    },
    callbackType: "STREAM",
    openSpaceId,
    userIdType: 1,
    // Group delivery: robot sends to group conversation
    imGroupOpenDeliverModel: isGroup
      ? { robotCode, spaceType: "IM_GROUP" }
      : undefined,
    imGroupOpenSpaceModel: isGroup
      ? { supportForward: true }
      : undefined,
    // Private delivery: robot sends to user's DM
    imRobotOpenDeliverModel: !isGroup
      ? { spaceType: "IM_ROBOT", robotCode }
      : undefined,
    imRobotOpenSpaceModel: !isGroup
      ? { supportForward: true }
      : undefined,
  };

  console.info("[approval-card-service] createAndDeliver", {
    outTrackId,
    deliveryMode: params.deliveryMode,
    conversationId: params.conversationId,
    resolvedConversationId: resolvedConversationId !== params.conversationId ? resolvedConversationId : undefined,
    title: params.title,
  });

  try {
    const res = await fetch(DINGTALK_CARD_API + "/createAndDeliver", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-acs-dingtalk-access-token": token,
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      const errorMsg = "createAndDeliver failed: HTTP " + res.status + " " + text.slice(0, 500);
      console.error("[approval-card-service] createAndDeliver error", {
        outTrackId,
        status: res.status,
        body: text.slice(0, 200),
      });
      return { ok: false, error: errorMsg };
    }

    const data = await res.json() as Record<string, unknown>;
    const cardInstanceId = typeof data.cardInstanceId === "string"
      ? data.cardInstanceId
      : undefined;

    console.info("[approval-card-service] createAndDeliver success", {
      outTrackId,
      cardInstanceId,
    });

    return {
      ok: true,
      outTrackId,
      cardInstanceId,
    };
  } catch (err) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    console.error("[approval-card-service] createAndDeliver threw", {
      outTrackId,
      error: errorMsg,
    });
    return { ok: false, error: errorMsg };
  }
}

// ── updateCardVariables API ────────────────────────────────────────────

/**
 * Update variables on an existing DingTalk interactive card.
 * Uses PUT /v1.0/card/instances to change card variable values
 * (e.g., hide buttons, update status text after approval).
 * Returns the HTTP status code from the API.
 */
export async function updateApprovalCardVariables(
  token: string,
  outTrackId: string,
  variables: Record<string, string>,
): Promise<number> {
  const body = {
    outTrackId,
    cardData: {
      cardParamMap: variables,
    },
    cardUpdateOptions: {
      updateCardDataByKey: true,
      updatePrivateDataByKey: true,
    },
  };

  console.info("[approval-card-service] updateCardVariables", {
    outTrackId,
    keys: Object.keys(variables),
  });

  const res = await fetch(DINGTALK_CARD_API, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "x-acs-dingtalk-access-token": token,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    console.error("[approval-card-service] updateCardVariables error", {
      outTrackId,
      status: res.status,
      body: text.slice(0, 200),
    });
  }

  return res.status;
}