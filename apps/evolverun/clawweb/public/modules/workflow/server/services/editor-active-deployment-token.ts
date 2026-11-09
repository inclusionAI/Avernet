import { createHash } from 'node:crypto';

/** The database row cannot safely be bound to an active deployment token. */
export class InvalidEditorActiveDeploymentTokenInputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'InvalidEditorActiveDeploymentTokenInputError';
  }
}

/** Minimal shared SQL projection for an active deployment baseline. */
export type EditorActiveDeploymentTokenRow = {
  pack_id: string;
  deploy_number: number | string;
  version: number | string;
  tag_name: string | null;
  action: string;
  spec_json: string;
};

function integer(value: number | string): string {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || value < 0) throw new InvalidEditorActiveDeploymentTokenInputError('Invalid active workflow token integer');
    return String(value);
  }
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value)) {
    throw new InvalidEditorActiveDeploymentTokenInputError('Invalid active workflow token integer');
  }
  return value;
}

/**
 * Hash this fixed, versioned tuple, not the SQL row object. Extra columns,
 * column order and timestamps do not affect it. spec_json is the exact stored
 * text as decoded by the DB driver, never parsed or reserialized. U+FFFD is
 * rejected to avoid binding ambiguous invalid UTF-8, including legitimate
 * U+FFFD; this is not a guarantee of original database byte identity.
 * workflowId is the identity used to query
 * the row. The caller must pass a row read from the DB, not client input.
 *
 * When authorizing a write, compute again from the current active row inside
 * the same transaction and lock that protects the subsequent write.
 */
export function editorActiveDeploymentToken(workflowId: string, row: EditorActiveDeploymentTokenRow): string {
  if (typeof workflowId !== 'string' || !row || typeof row !== 'object' || typeof row.pack_id !== 'string'
    || (row.tag_name !== null && typeof row.tag_name !== 'string')
    || typeof row.action !== 'string' || typeof row.spec_json !== 'string') {
    throw new InvalidEditorActiveDeploymentTokenInputError('Invalid active workflow token row');
  }
  // A driver may replace distinct invalid UTF-8 byte sequences with U+FFFD.
  // Even legitimate U+FFFD is rejected because it cannot be distinguished here.
  if ([workflowId, row.pack_id, row.tag_name, row.action, row.spec_json]
    .some(value => typeof value === 'string' && value.includes('\uFFFD'))) {
    throw new InvalidEditorActiveDeploymentTokenInputError('Ambiguous active workflow token text');
  }
  return createHash('sha256').update(JSON.stringify([
    'active-workflow-baseline-v1', workflowId, row.pack_id, integer(row.deploy_number),
    integer(row.version), row.tag_name, row.action, row.spec_json,
  ]), 'utf8').digest('hex');
}
