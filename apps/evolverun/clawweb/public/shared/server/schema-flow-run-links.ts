import type { IDatabase } from './db.js';

// This targeted MySQL DDL must bypass the generic renderer, which deliberately
// shortens VARCHAR(255) to VARCHAR(190). Full indexes require a sufficiently
// large InnoDB index-key limit; never replace them with prefix uniqueness.
export const flowRunLinkMysqlUpgradeDdl = `ALTER TABLE flow_run_links
  MODIFY COLUMN child_flow_id VARCHAR(255) NOT NULL,
  MODIFY COLUMN parent_flow_id VARCHAR(255) NOT NULL,
  MODIFY COLUMN parent_node_id VARCHAR(255) NOT NULL,
  MODIFY COLUMN child_workflow_id VARCHAR(255) NOT NULL`;

/** Fail closed when a managed or externally provisioned schema is too narrow. */
export async function assertFlowRunLinkMysqlSchema(db: IDatabase): Promise<void> {
  // Some servers may silently shorten an index. Verify the actual schema
  // before recording the migration; failure must keep writes unavailable.
  const columns = await db.query<{ column_name: string; max_length: number | string }>(
    `SELECT COLUMN_NAME AS column_name, CHARACTER_MAXIMUM_LENGTH AS max_length
     FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_run_links'`,
  );
  const names = ['child_flow_id', 'parent_flow_id', 'parent_node_id', 'child_workflow_id'];
  if (!names.every(name => columns.some(column => column.column_name === name && Number(column.max_length) >= 255))) {
    throw new Error('flow_run_links identifier columns are not fully provisioned');
  }
  const indexes = await db.query<{ index_name: string; column_name: string; sub_part: number | null; non_unique: number | string; seq: number | string }>(
    `SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name, SUB_PART AS sub_part,
            NON_UNIQUE AS non_unique, SEQ_IN_INDEX AS seq
     FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flow_run_links'
       AND INDEX_NAME IN ('PRIMARY', 'idx_flow_run_links_parent')`,
  );
  const fullIndex = (indexName: string, columnName: string, unique: boolean) => {
    const entries = indexes.filter(index => index.index_name === indexName);
    return entries.length === 1 && entries[0].column_name === columnName && entries[0].sub_part === null
      && Number(entries[0].seq) === 1 && (!unique || Number(entries[0].non_unique) === 0);
  };
  if (!fullIndex('PRIMARY', 'child_flow_id', true) || !fullIndex('idx_flow_run_links_parent', 'parent_flow_id', false)) {
    throw new Error('flow_run_links indexes are not fully provisioned');
  }
}
