import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { FlowRunLinkRepository, type FlowRunLink } from '@avernet/clawweb-shared/server/repositories/flow-run-link-repository';

/** The host must derive this identity from verified credentials, never request JSON. */
export type LinkPrincipal = { botId: string; ownerId: string };
export type LinkRun = { flowId: string; workflowId: string };

/** Authorizes this specific relationship, not merely access to an internal API.
 * The host must verify the principal is entitled to link these exact runs and
 * workflows and that the node/attempt is legitimate, using trusted authority
 * data; caller-controlled run metadata and request JSON are not authority.
 * Both production and trial runs may be supplied. */
export type AuthorizeRunLink = (principal: LinkPrincipal, relation: {
  parent: LinkRun; child: LinkRun; parentNodeId: string; parentAttempt: number | null;
}, db: IDatabase) => Promise<boolean>;

const validId = (v: unknown): v is string => typeof v === 'string' && v.length > 0
  && v.length <= 255 && !/[\s\x00-\x1f\x7f/\\]/u.test(v);

export function parseRunLink(value: unknown): FlowRunLink | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const obj = value as Record<string, unknown>;
  const keys = ['childFlowId', 'parentFlowId', 'parentNodeId', 'parentAttempt', 'childWorkflowId'];
  if (Object.keys(obj).length !== keys.length || !keys.every(key => Object.hasOwn(obj, key))) return null;
  if (![obj.childFlowId, obj.parentFlowId, obj.parentNodeId, obj.childWorkflowId].every(validId)
      || obj.childFlowId === obj.parentFlowId
      || !(obj.parentAttempt === null || typeof obj.parentAttempt === 'number' && Number.isSafeInteger(obj.parentAttempt)
        && obj.parentAttempt > 0 && obj.parentAttempt <= 2_147_483_647)) return null;
  return obj as FlowRunLink;
}

const sameLink = (a: FlowRunLink, b: FlowRunLink) => a.childFlowId === b.childFlowId
  && a.parentFlowId === b.parentFlowId && a.parentNodeId === b.parentNodeId
  && a.parentAttempt === b.parentAttempt && a.childWorkflowId === b.childWorkflowId;

class DuplicateLinkInsertError extends Error {}

/** Rechecks authorization on every retry. Never changes an existing binding. */
export async function registerRunLink(db: IDatabase, principal: LinkPrincipal, link: FlowRunLink,
  authorize: AuthorizeRunLink): Promise<'ok' | 'not_found'> {
  const run = () => db.transaction(async tx => {
    const rows = await tx.query<{ flow_id: string; workflow_id: string }>(
      `SELECT flow_id, workflow_id FROM flow_runs WHERE flow_id IN (?, ?) ORDER BY flow_id${tx.dbType === 'mysql' || tx.dbType === 'zdas' ? ' FOR UPDATE' : ''}`,
      [link.parentFlowId, link.childFlowId]);
    const parent = rows.find(row => row.flow_id === link.parentFlowId);
    const child = rows.find(row => row.flow_id === link.childFlowId);
    if (!parent || !child || !validId(parent.workflow_id) || child.workflow_id !== link.childWorkflowId) return 'not_found' as const;
    const allowed = await authorize(principal, {
      parent: { flowId: parent.flow_id, workflowId: parent.workflow_id },
      child: { flowId: child.flow_id, workflowId: child.workflow_id },
      parentNodeId: link.parentNodeId, parentAttempt: link.parentAttempt,
    }, tx);
    if (allowed !== true) return 'not_found' as const;
    const repo = new FlowRunLinkRepository(tx);
    const existing = await repo.findByChild(link.childFlowId);
    if (existing) return sameLink(existing, link) ? 'ok' as const : 'not_found' as const;
    try {
      await repo.insert(link);
    } catch (error) {
      const code = (error as { code?: unknown } | null)?.code;
      if (code === 'SQLITE_CONSTRAINT_PRIMARYKEY' || code === 'SQLITE_CONSTRAINT_UNIQUE' || code === 'ER_DUP_ENTRY') {
        throw new DuplicateLinkInsertError();
      }
      throw error;
    }
    return 'ok' as const;
  });
  try { return await run(); } catch (error) {
    // Competing writers may both observe no link. Retry from a fresh transaction
    // only for database uniqueness violations, never for arbitrary write errors.
    if (!(error instanceof DuplicateLinkInsertError)) throw error;
    return run();
  }
}
