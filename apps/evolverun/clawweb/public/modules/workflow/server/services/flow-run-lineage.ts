import type { IDatabase } from '@avernet/clawweb-shared/server/db';

/** Engine-reported metadata only. Neither field grants access to an ancestor or
 * proves a relationship; parent_relation and request associations own that authority. */
export type FlowRunLineage = { parent_flow_id: string | null; root_flow_id: string | null };
export type FlowRunLineageInput = { parentFlowId?: string | null; rootFlowId?: string | null };
export class FlowRunLineageError extends Error {}

function lineageId(value: unknown, field: string): string | null {
  if (value == null) return null;
  if (typeof value !== 'string' || !value.trim() || Array.from(value).length > 255) {
    throw new FlowRunLineageError(`${field} must be null or a nonblank string of at most 255 characters`);
  }
  // UTF-8 database drivers replace unpaired UTF-16 surrogates. Reject them
  // instead of silently changing the asserted ID or breaking exact replays.
  if (Array.from(value).some(char => char.length === 1 && char.charCodeAt(0) >= 0xD800 && char.charCodeAt(0) <= 0xDFFF)) {
    throw new FlowRunLineageError(`${field} must be valid Unicode (no unpaired surrogates)`);
  }
  return value; // Do not trim, normalize Unicode, truncate, or derive missing IDs.
}

export function normalizeFlowRunLineage(input: { parentFlowId?: unknown; rootFlowId?: unknown }): FlowRunLineage {
  return { parent_flow_id: lineageId(input.parentFlowId, 'parent_flow_id'),
    root_flow_id: lineageId(input.rootFlowId, 'root_flow_id') };
}

/** Compare only supplied fields against the winning row, never mutable state or
 * request-parent mechanisms. Omission is not a backfill request; explicit null
 * asserts unknown. Keep existing request fingerprints byte-for-byte compatible. */
export async function runLineageMatches(db: IDatabase, flowId: string, body: Record<string, unknown>): Promise<boolean> {
  const supplied = ['parent_flow_id', 'root_flow_id'].filter(field => body[field] !== undefined);
  if (!supplied.length) return true;
  const proposed = normalizeFlowRunLineage({ parentFlowId: body.parent_flow_id, rootFlowId: body.root_flow_id });
  const [row] = await db.query<FlowRunLineage & { flow_id: string }>(
    `SELECT flow_id, parent_flow_id, root_flow_id FROM flow_runs WHERE flow_id = ?${db.dbType === 'mysql' || db.dbType === 'zdas' ? ' FOR UPDATE' : ''}`, [flowId]);
  return !!row && row.flow_id === flowId && supplied.every(field => row[field] === proposed[field]);
}
