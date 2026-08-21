/**
 * Callback token registry — CRUD operations for the callback_tokens table.
 *
 * Each async-callback node gets a unique UUID v4 token when it enters waiting
 * state. The token is single-use: consumed on first successful callback or
 * expired by the timeout poller.
 *
 * @module callback/token-registry
 */

import crypto from "node:crypto";
import type { IDatabase } from "../db/types.js";

// ── Types ──

export type CallbackTokenStatus = "pending" | "consumed" | "expired";

export type CallbackTokenRecord = {
  id: number;
  token: string;
  flowId: string;
  nodeId: string;
  workflowId: string | null;
  status: CallbackTokenStatus;
  callbackResult: string | null;
  callbackHeaders: string | null;
  callbackIp: string | null;
  callbackUserId: string | null;
  timeoutAt: number | null;
  createdAt: number;
  consumedAt: number | null;
  expiredAt: number | null;
};

// ── Helpers ──

/** Generate a cryptographically random UUID v4 token (128-bit entropy). */
export function generateCallbackToken(): string {
  return crypto.randomUUID();
}

/** Parse a human-readable duration string (e.g. "30m", "2h", "24h") to epoch seconds. */
export function parseTimeoutToEpoch(timeout: string, nowMs?: number): number {
  const now = nowMs ?? Date.now();
  const match = /^(\d+)\s*(s|m|h|d)$/.exec(timeout.trim().toLowerCase());
  if (!match) {
    throw new Error(`Invalid timeout format: "${timeout}". Use e.g. "30m", "2h", "24h".`);
  }
  const value = parseInt(match[1]!, 10);
  const unit = match[2]!;
  const multipliers: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400 };
  const seconds = value * (multipliers[unit] ?? 0);
  return Math.floor(now / 1000) + seconds;
}

function mapRow(row: Record<string, unknown>): CallbackTokenRecord {
  return {
    id: row.id as number,
    token: row.token as string,
    flowId: row.flow_id as string,
    nodeId: row.node_id as string,
    workflowId: (row.workflow_id as string) ?? null,
    status: row.status as CallbackTokenStatus,
    callbackResult: row.callback_result as string | null,
    callbackHeaders: row.callback_headers as string | null,
    callbackIp: (row.callback_ip as string) ?? null,
    callbackUserId: (row.callback_user_id as string) ?? null,
    timeoutAt: (row.timeout_at as number) ?? null,
    createdAt: row.created_at as number,
    consumedAt: (row.consumed_at as number) ?? null,
    expiredAt: (row.expired_at as number) ?? null,
  };
}

// ── Registry ──

export type CallbackTokenRegistry = {
  /** Insert a new pending callback token. */
  create(params: {
    flowId: string;
    nodeId: string;
    workflowId?: string;
    timeoutAt?: number;
  }): Promise<string>;

  /** Look up a token record by token value. */
  findByToken(token: string): Promise<CallbackTokenRecord | null>;

  /** Mark a token as consumed with the callback result. */
  consume(
    token: string,
    result: Record<string, unknown>,
    meta?: { headers?: string; ip?: string; userId?: string },
  ): Promise<boolean>;

  /** Mark a token as expired (called by timeout poller). */
  expire(token: string): Promise<boolean>;

  /** Find all pending tokens that have passed their timeout_at. */
  findExpiredPending(): Promise<CallbackTokenRecord[]>;

  /** Delete tokens older than the retention period. */
  deleteOlderThan(retentionDays: number): Promise<number>;
};

export function createCallbackTokenRegistry(db: IDatabase): CallbackTokenRegistry {
  return {
    async create(params) {
      const token = generateCallbackToken();
      await db.exec(
        `INSERT INTO callback_tokens (token, flow_id, node_id, workflow_id, status, timeout_at)
         VALUES (?, ?, ?, ?, 'pending', ?)`,
        [token, params.flowId, params.nodeId, params.workflowId ?? null, params.timeoutAt ?? null],
      );
      return token;
    },

    async findByToken(token) {
      const rows = await db.query(
        `SELECT * FROM callback_tokens WHERE token = ?`,
        [token],
      );
      if (rows.length === 0) return null;
      return mapRow(rows[0]!);
    },

    async consume(token, result, meta) {
      const now = Math.floor(Date.now() / 1000);
      const { affectedRows } = await db.exec(
        `UPDATE callback_tokens
         SET status = 'consumed',
             callback_result = ?,
             callback_headers = ?,
             callback_ip = ?,
             callback_user_id = ?,
             consumed_at = ?
         WHERE token = ? AND status = 'pending'`,
        [
          JSON.stringify(result),
          meta?.headers ?? null,
          meta?.ip ?? null,
          meta?.userId ?? null,
          now,
          token,
        ],
      );
      return affectedRows > 0;
    },

    async expire(token) {
      const now = Math.floor(Date.now() / 1000);
      const { affectedRows } = await db.exec(
        `UPDATE callback_tokens
         SET status = 'expired', expired_at = ?
         WHERE token = ? AND status = 'pending'`,
        [now, token],
      );
      return affectedRows > 0;
    },

    async findExpiredPending() {
      const now = Math.floor(Date.now() / 1000);
      const rows = await db.query(
        `SELECT * FROM callback_tokens
         WHERE status = 'pending' AND timeout_at IS NOT NULL AND timeout_at <= ?`,
        [now],
      );
      return rows.map(mapRow);
    },

    async deleteOlderThan(retentionDays) {
      const cutoff = Math.floor(Date.now() / 1000) - retentionDays * 86400;
      const { affectedRows } = await db.exec(
        `DELETE FROM callback_tokens
         WHERE status IN ('consumed', 'expired') AND gmt_modified IS NOT NULL AND gmt_modified < ?`,
        [cutoff],
      );
      return affectedRows;
    },
  };
}