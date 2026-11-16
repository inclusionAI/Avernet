import { createHash } from 'node:crypto';

/** Shared current-value token for draft reads, writes and Editor deployments. */
export function editorDraftToken(row: {
  id: number; workflow_id: string; pack_id: string | null; spec_json: string;
  version: number | null; title: string | null; owner_id: string | null;
  gmt_create: number | string; gmt_modified: number | string;
}): string {
  return createHash('sha256').update(JSON.stringify([
    'editor-draft-current-value-v1', row.id, row.workflow_id, row.pack_id, row.spec_json,
    row.version, row.title, row.owner_id, row.gmt_create, row.gmt_modified,
  ])).digest('hex');
}
