import { migrations } from '../schema.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { ordinaryRunRequestMigrations } from '../schema-ordinary-run-request.js';
import { distributedLockMigrations } from '../schema-distributed-lock.js';
import { aistudioTrainMigrations } from '../schema-aistudio-train.js';

/** Unified registrations, freshly collected per run to preserve upstream registry behavior. */
export function collectMigrations() {
  // v155/v156 are retired no-DDL tombstones, retained for ledger compatibility.
  const registry = [...migrations, ...workflowSnapshotMigrations, ...ordinaryRunRequestMigrations, ...aistudioTrainMigrations, ...distributedLockMigrations]
    .sort((left, right) => left.version - right.version);
  for (const { version } of aistudioTrainMigrations) {
    if (registry.filter(migration => migration.version === version).length !== 1) {
      throw new Error(`Conflicting AIStudio migration version ${version}`);
    }
  }
  for (const { version } of distributedLockMigrations) {
    if (registry.filter(migration => migration.version === version).length !== 1)
      throw new Error('Conflicting distributed lock migration version ' + version);
  }
  return registry;
}
export const allMigrations = collectMigrations();
