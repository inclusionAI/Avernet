import { migrations } from '../schema.js';
import { workflowSnapshotMigrations } from '../schema-workflow-snapshot.js';
import { ordinaryRunRequestMigrations } from '../schema-ordinary-run-request.js';
import { aistudioTrainMigrations } from '../schema-aistudio-train.js';

/** Unified registrations, freshly collected per run to preserve upstream registry behavior. */
export function collectMigrations() {
  const registry = [...migrations, ...workflowSnapshotMigrations, ...ordinaryRunRequestMigrations, ...aistudioTrainMigrations]
    .sort((left, right) => left.version - right.version);
  for (const { version } of aistudioTrainMigrations) {
    if (registry.filter(migration => migration.version === version).length !== 1) {
      throw new Error(`Conflicting AIStudio migration version ${version}`);
    }
  }
  return registry;
}
export const allMigrations = collectMigrations();
