/**
 * Approval card registry — maps DingTalk interactive card outTrackId to
 * ClawFlow approval state (flowId, nodeId, approverIds, approvalPolicy).
 *
 * Storage: JSON file at ~/.openclaw/workflow/approval-cards.json with
 * in-memory cache. Atomic writes via writeFile + rename.
 *
 * TTL: 24 hours (approvals older than this are pruned on access/cleanup).
 */

import { existsSync, readFileSync, writeFileSync, renameSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";

// ── Types ──────────────────────────────────────────────────────────────

export type ApprovalPolicy = "any" | "all" | "majority";

export type ApprovalCardStatus = "pending" | "approved" | "rejected";

export type ApprovalCardRecord = {
  /** Card instance tracking ID (primary key) */
  outTrackId: string;
  /** ClawFlow flow ID */
  flowId: string;
  /** Approval node ID within the workflow */
  nodeId: string;
  /** Workflow spec ID */
  workflowId: string;
  /** List of empId strings for authorized approvers */
  approverIds: string[];
  /** Approval policy: any = first action wins, all = unanimous, majority = >50% */
  approvalPolicy: ApprovalPolicy;
  /** empIds of users who have approved */
  approvedBy: string[];
  /** empIds of users who have rejected */
  rejectedBy: string[];
  /** Where the card was delivered (conversationId or userId) */
  conversationId: string;
  /** Delivery mode */
  deliveryMode: "private" | "dingtalk-group" | "card-web";
  /** Timestamp when the card was created */
  createdAt: number;
  /** Timestamp when the approval was resolved (undefined while pending) */
  resolvedAt?: number;
  /** Current status */
  status: ApprovalCardStatus;
};

// ── Constants ──────────────────────────────────────────────────────────

const DEFAULT_MAX_AGE_MS = 24 * 60 * 60 * 1000; // 24 hours
const REGISTRY_DIR = join(homedir(), ".openclaw", "workflow");
const REGISTRY_PATH = join(REGISTRY_DIR, "approval-cards.json");

// ── In-memory cache ────────────────────────────────────────────────────

let cache: Map<string, ApprovalCardRecord> | null = null;
let cacheLoadedAt = 0;
const CACHE_TTL_MS = 5_000; // Re-read from disk every 5s at most

// ── Persistence ────────────────────────────────────────────────────────

function loadRegistry(): Map<string, ApprovalCardRecord> {
  const now = Date.now();
  if (cache && now - cacheLoadedAt < CACHE_TTL_MS) {
    return cache;
  }

  const records = new Map<string, ApprovalCardRecord>();
  try {
    if (existsSync(REGISTRY_PATH)) {
      const raw = readFileSync(REGISTRY_PATH, "utf-8");
      const data = JSON.parse(raw) as Array<ApprovalCardRecord>;
      for (const record of data) {
        records.set(record.outTrackId, record);
      }
    }
  } catch (err) {
    console.warn("[approval-card-registry] Failed to load registry, starting fresh", {
      error: err instanceof Error ? err.message : String(err),
    });
  }

  cache = records;
  cacheLoadedAt = now;
  return records;
}

function saveRegistry(records: Map<string, ApprovalCardRecord>): void {
  try {
    const data = Array.from(records.values());
    const tmpPath = REGISTRY_PATH + ".tmp";
    mkdirSync(dirname(REGISTRY_PATH), { recursive: true });
    writeFileSync(tmpPath, JSON.stringify(data, null, 2), "utf-8");
    renameSync(tmpPath, REGISTRY_PATH);
    // Cache is now in sync
    cache = records;
    cacheLoadedAt = Date.now();
  } catch (err) {
    console.error("[approval-card-registry] Failed to save registry", {
      error: err instanceof Error ? err.message : String(err),
    });
  }
}

function invalidateCache(): void {
  cache = null;
  cacheLoadedAt = 0;
}

// ── Policy evaluation ──────────────────────────────────────────────────

export type PolicyEvaluationResult = {
  /** Whether the approval policy has been met */
  passed: boolean;
  /** The final status if passed, or current status if still pending */
  status: ApprovalCardStatus;
  /** Human-readable reason */
  reason: string;
};

export function evaluateApprovalPolicy(record: ApprovalCardRecord): PolicyEvaluationResult {
  const { approvalPolicy, approverIds, approvedBy, rejectedBy } = record;
  const total = approverIds.length;

  // Any rejection immediately resolves as "rejected" for "all" and "majority" policies
  if (approvalPolicy !== "any" && rejectedBy.length > 0) {
    return {
      passed: true,
      status: "rejected",
      reason: `${rejectedBy.length}/${total} 审批人已驳回`,
    };
  }

  switch (approvalPolicy) {
    case "any": {
      // First action wins: approve → approved, reject → rejected
      if (approvedBy.length > 0) {
        return { passed: true, status: "approved", reason: `${approvedBy[0]} 已同意` };
      }
      if (rejectedBy.length > 0) {
        return { passed: true, status: "rejected", reason: `${rejectedBy[0]} 已驳回` };
      }
      return { passed: false, status: "pending", reason: "等待审批" };
    }

    case "all": {
      // All approverIds must be in approvedBy
      if (approvedBy.length >= total) {
        return { passed: true, status: "approved", reason: `全部 ${total} 位审批人已同意` };
      }
      return {
        passed: false,
        status: "pending",
        reason: `已同意 ${approvedBy.length}/${total}，需全部同意`,
      };
    }

    case "majority": {
      // More than half must approve
      const threshold = Math.floor(total / 2) + 1;
      if (approvedBy.length >= threshold) {
        return {
          passed: true,
          status: "approved",
          reason: `${approvedBy.length}/${total} 审批人已同意（超过半数）`,
        };
      }
      return {
        passed: false,
        status: "pending",
        reason: `已同意 ${approvedBy.length}/${total}，需 ${threshold} 人同意`,
      };
    }

    default:
      return { passed: false, status: "pending", reason: `未知审批策略: ${approvalPolicy}` };
  }
}

// ── Public API ─────────────────────────────────────────────────────────

/** Register a new approval card. Overwrites any existing record with the same outTrackId. */
export function registerApprovalCard(record: ApprovalCardRecord): void {
  const records = loadRegistry();
  records.set(record.outTrackId, record);
  saveRegistry(records);
  console.info("[approval-card-registry] Registered", {
    outTrackId: record.outTrackId,
    flowId: record.flowId,
    nodeId: record.nodeId,
    deliveryMode: record.deliveryMode,
  });
}

/** Look up an approval card by outTrackId. Returns null if not found or expired. */
export function resolveApprovalCard(outTrackId: string): ApprovalCardRecord | null {
  const records = loadRegistry();
  const record = records.get(outTrackId);
  if (!record) return null;

  // Prune expired records
  if (Date.now() - record.createdAt > DEFAULT_MAX_AGE_MS) {
    records.delete(outTrackId);
    saveRegistry(records);
    return null;
  }

  return record;
}

/**
 * Record an approval/rejection action by a user.
 * Idempotent: if the user already performed this action, returns the current record.
 * Returns null if the outTrackId is not found or the card is already resolved.
 */
export function recordApprovalAction(
  outTrackId: string,
  userId: string,
  action: "approve" | "reject",
): ApprovalCardRecord | null {
  const records = loadRegistry();
  const record = records.get(outTrackId);
  if (!record) return null;
  if (record.status !== "pending") return null;

  // Idempotent check: user already performed this exact action
  if (action === "approve" && record.approvedBy.includes(userId)) {
    return record;
  }
  if (action === "reject" && record.rejectedBy.includes(userId)) {
    return record;
  }

  // Mutate (we're updating the record in the Map — safe since we save immediately)
  if (action === "approve") {
    record.approvedBy = [...record.approvedBy, userId];
    // Remove from rejectedBy if switching (edge case)
    record.rejectedBy = record.rejectedBy.filter((id) => id !== userId);
  } else {
    record.rejectedBy = [...record.rejectedBy, userId];
    // Remove from approvedBy if switching (edge case)
    record.approvedBy = record.approvedBy.filter((id) => id !== userId);
  }

  records.set(outTrackId, record);
  saveRegistry(records);
  return record;
}

/** Mark an approval card as resolved (approved or rejected). */
export function markApprovalCardResolved(outTrackId: string, status: "approved" | "rejected"): void {
  const records = loadRegistry();
  const record = records.get(outTrackId);
  if (!record) return;

  record.status = status;
  record.resolvedAt = Date.now();
  records.set(outTrackId, record);
  saveRegistry(records);
}

/** Remove expired approval cards from the registry. Returns the number of pruned records. */
export function cleanupExpiredApprovalCards(maxAgeMs: number = DEFAULT_MAX_AGE_MS): number {
  const records = loadRegistry();
  const now = Date.now();
  let pruned = 0;

  for (const [outTrackId, record] of records) {
    if (now - record.createdAt > maxAgeMs) {
      records.delete(outTrackId);
      pruned++;
    }
  }

  if (pruned > 0) {
    saveRegistry(records);
  }

  return pruned;
}