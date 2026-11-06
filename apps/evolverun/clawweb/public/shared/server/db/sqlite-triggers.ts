import type { IDatabase } from '../db.js';
import { sqliteTriggers } from '../schema.js';

// Only the grammar used by our trigger definitions is accepted. Quoted ASCII
// identifiers compare as identifiers; string literals, comments, and all other
// syntax are rejected rather than normalized into a different expression.
function triggerTokens(sql: string): string[] | null {
  const tokens: string[] = [];
  const lexer = /\s+|[A-Za-z_][A-Za-z_0-9]*|"[A-Za-z_][A-Za-z_0-9]*"|[().=;]/gy;
  for (let offset = 0; offset < sql.length;) {
    lexer.lastIndex = offset;
    const lexeme = lexer.exec(sql)?.[0];
    if (!lexeme) return null;
    offset = lexer.lastIndex;
    if (!/^\s+$/.test(lexeme)) tokens.push(lexeme.replace(/^"|"$/g, '').toLowerCase());
  }
  if (tokens.slice(0, 5).join(' ') === 'create trigger if not exists') tokens.splice(2, 3);
  return tokens;
}

/** Check existing trigger definitions without taking a write lock. Missing tables
 * occur in older/partial schemas; only triggers for present tables are required. */
export async function ensureSqliteTriggers(db: IDatabase, allowCreate: boolean): Promise<void> {
  const objects = await db.query<{ type: string; name: string; tbl_name: string; sql: string | null }>(
    "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type IN ('table', 'trigger')",
  );
  const tables = new Set(objects.filter(object => object.type === 'table').map(object => object.name));
  const triggers = new Map(objects.filter(object => object.type === 'trigger').map(object => [object.name, object]));
  const absentTables: string[] = [];
  for (const ddl of sqliteTriggers) {
    const match = /^CREATE TRIGGER IF NOT EXISTS (\w+) AFTER UPDATE ON (\w+)\b/i.exec(ddl);
    if (!match) throw new Error('Unrecognized SQLite trigger definition');
    const [, name, table] = match;
    if (!tables.has(table)) { absentTables.push(table); continue; }
    let existing = triggers.get(name);
    if (!existing) {
      if (!allowCreate) throw new Error(`SQLite trigger ${name} is missing after migration was deferred`);
      // Stop at the first failure rather than spending one busy_timeout per trigger.
      await db.exec(ddl);
      existing = (await db.query<typeof objects[number]>(
        "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", [name],
      ))[0];
    }
    const expected = triggerTokens(ddl);
    const actual = existing?.sql ? triggerTokens(existing.sql) : null;
    if (!existing || existing.tbl_name !== table || !actual || !expected || actual.join(' ') !== expected.join(' ')) {
      throw new Error(`SQLite trigger ${name} is missing or incompatible`);
    }
  }
  if (absentTables.length) console.warn(`[clawweb] SQLite triggers skipped for absent tables: ${absentTables.join(', ')}`);
}
