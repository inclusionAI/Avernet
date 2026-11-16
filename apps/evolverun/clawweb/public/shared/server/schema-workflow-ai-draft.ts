import type { Migration } from './schema-types.js';

/** v153 shipped in earlier hosts. Reserve its ledger slot without creating or
 * dropping a table: Editor AI drafts now use BaaS message state and signed
 * receipts. Any already-provisioned legacy records remain untouched. */
export const workflowAiDraftMigrations: readonly Migration[] = [{
  version: 153,
  description: 'Reserve retired Editor AI draft table migration; use BaaS receipts',
  sql: [],
}];
