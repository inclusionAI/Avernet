import type Database from 'better-sqlite3';
import { migrationsV1To36 } from '../schema-v1-36.js';
import { sqliteTriggers } from '../schema.js';

/** Restore the v15 prerequisite omitted by partial historical schemas, without advancing their ledger. */
export function seedLegacyApprovalCards(raw: Database.Database): void {
  raw.exec(migrationsV1To36.find(migration => migration.version === 15)!.sql[0]);
  // Historical installations already have this trigger, including while optional DDL is deferred.
  raw.exec(sqliteTriggers.find(ddl => ddl.includes('trg_approval_cards_update'))!);
}
