/**
 * Database dialect abstraction.
 *
 * Encapsulates per-database differences so that Repository code no longer
 * branches on `dbType === "mysql"`. Each supported runtime target gets its
 * own Dialect implementation.
 */

export type DbType = "sqlite" | "mysql" | "zdas";

export interface TypeMapping {
  autoIncrementPk: string;
  bigAutoIncrementPk: string;
  timestamp: string;
  timestampNullable: string;
  longText: string;
  decimal(precision: number, scale: number): string;
  json(): string;
}

export interface Dialect {
  readonly name: DbType;
  readonly driver: "better-sqlite3" | "mysql2";

  /** Quote an identifier (table/column name). */
  quote(identifier: string): string;

  /** Current timestamp value in the database-native form. */
  now(): string | number;

  /** Convert an epoch second value to the database-native form. */
  epochToDb(ts: number | null | undefined): unknown;

  /** Convert a database-native timestamp value back to epoch seconds. */
  dbToEpoch(value: unknown): number | null;

  /** Per-database SQL type names. */
  readonly typeMapping: TypeMapping;

  /** Whether the dialect supports `INSERT ... RETURNING`. */
  readonly supportsReturning: boolean;

  /** Max index key length in bytes (relevant for prefix indexes). */
  readonly maxIndexKeyLength: number;

  /**
   * True when standalone `CREATE INDEX` is not supported and indexes must be
   * declared inline in `CREATE TABLE` or via `ALTER TABLE ADD INDEX`.
   * This is true for ZDAS/OceanBase.
   */
  readonly inlineIndexesOnly: boolean;

  /**
   * Render canonical DDL for this dialect.
   *
   * Canonical DDL is expressed in SQLite-compatible SQL. Each dialect
   * converts it to its own syntax. This replaces the previous regex-based
   * `adaptDdl` approach with a structured, dialect-aware renderer.
   */
  renderDdl(sql: string): string;

  /**
   * Column type cache (MySQL only). Populated from CREATE TABLE statements
   * to inform CREATE INDEX prefix decisions. Other dialects may omit this.
   */
  columnTypes?: Map<string, ColumnTypeInfo>;
}

/**
 * Split a string by a delimiter, ignoring delimiters that appear inside
 * balanced parentheses. Used to separate CREATE TABLE clauses without
 * breaking index/column expressions that contain commas.
 */
function splitTopLevel(value: string, delimiter: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let inString = false;
  let stringChar = "";
  let current = "";
  for (let i = 0; i < value.length; i++) {
    const ch = value[i];
    if (inString) {
      current += ch;
      if (ch === stringChar) {
        // Handle escaped quotes (''' is not an escape in SQL, but '' is).
        if (value[i - 1] !== "\\") {
          inString = false;
          stringChar = "";
        }
      }
      continue;
    }
    if (ch === '"' || ch === "'") {
      inString = true;
      stringChar = ch;
      current += ch;
    } else if (ch === "(") {
      depth++;
      current += ch;
    } else if (ch === ")") {
      depth--;
      current += ch;
    } else if (value.substring(i, i + delimiter.length) === delimiter && depth === 0) {
      parts.push(current.trim());
      current = "";
      i += delimiter.length - 1;
    } else {
      current += ch;
    }
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

// ── SQLite dialect ──

export const sqliteDialect: Dialect = {
  name: "sqlite",
  driver: "better-sqlite3",

  quote: (identifier) => `"${identifier}"`,

  now: () => Math.floor(Date.now() / 1000),

  epochToDb: (ts) => ts ?? null,

  dbToEpoch: (value) => {
    if (value === null || value === undefined) return null;
    if (typeof value === "number") return value;
    if (typeof value === "string") {
      const parsed = Date.parse(value);
      return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
    }
    return null;
  },

  typeMapping: {
    autoIncrementPk: "INTEGER PRIMARY KEY AUTOINCREMENT",
    bigAutoIncrementPk: "INTEGER PRIMARY KEY AUTOINCREMENT",
    timestamp: "INTEGER NOT NULL DEFAULT (unixepoch())",
    timestampNullable: "INTEGER",
    longText: "TEXT",
    decimal: () => "INTEGER",
    json: () => "TEXT",
  },

  supportsReturning: true,
  maxIndexKeyLength: Number.MAX_SAFE_INTEGER,
  inlineIndexesOnly: false,

  renderDdl(sql: string): string {
    // Canonical DDL is already SQLite-compatible, but MySQL-only migrations
    // may appear in the migration list. Clean them up so they are harmless.

    // Extract inline INDEX / UNIQUE INDEX definitions from CREATE TABLE and emit
    // standalone CREATE INDEX statements or UNIQUE constraints. SQLite does not
    // support inline named INDEX, but does support inline UNIQUE constraints.
    const inlineIndexRegex = /^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(([\s\S]+)\)\s*(?:COMMENT\s*=\s*'[^']*')?\s*;?\s*$/im;
    const extraIndexes: string[] = [];
    const createTableMatch = sql.match(inlineIndexRegex);
    if (createTableMatch) {
      const tableName = createTableMatch[1];
      const body = createTableMatch[2];
      // Split body into top-level clauses (columns, constraints, indexes),
      // respecting string literals and balanced parentheses.
      const clauses = splitTopLevel(body, ",");
      const kept: string[] = [];
      for (const clause of clauses) {
        const plainIndexMatch = clause.match(/^(?:CONSTRAINT\s+\w+\s+)?INDEX\s+(\w+)\s*\(([^)]+)\)\s*$/i);
        const uniqueIndexMatch = clause.match(/^(?:CONSTRAINT\s+\w+\s+)?UNIQUE\s+(?:INDEX\s+)?(\w+)\s*\(([^)]+)\)\s*$/i);
        const uniqueNoNameMatch = clause.match(/^(?:CONSTRAINT\s+\w+\s+)?UNIQUE\s*\(([^)]+)\)\s*$/i);
        if (uniqueIndexMatch) {
          kept.push(`UNIQUE (${uniqueIndexMatch[2]})`);
        } else if (uniqueNoNameMatch) {
          kept.push(`UNIQUE (${uniqueNoNameMatch[1]})`);
        } else if (plainIndexMatch) {
          extraIndexes.push(`CREATE INDEX IF NOT EXISTS ${plainIndexMatch[1]} ON ${tableName} (${plainIndexMatch[2]})`);
        } else {
          kept.push(clause);
        }
      }
      sql = `CREATE TABLE IF NOT EXISTS ${tableName} (${kept.join(", ")})`;
    }

    const rendered = sql
      .replace(/\bBIGINT(?:\s*\(\d+\))?\s+PRIMARY\s+KEY\s+AUTO_INCREMENT\b/gi, "INTEGER PRIMARY KEY AUTOINCREMENT")
      .replace(/ALTER\s+TABLE\s+\w+\s+MODIFY\s+COLUMN\s+[\s\S]*?$/gm, "SELECT 0 -- SQLite: MODIFY COLUMN skipped (INTEGER is unbounded)")
      .replace(/\bTINYINT\b/gi, "INTEGER")
      .replace(/\bBIGINT\b/gi, "INTEGER")
      .replace(/TIMESTAMP\s+NOT\s+NULL\s+DEFAULT\s+CURRENT_TIMESTAMP\s+ON\s+UPDATE\s+CURRENT_TIMESTAMP/gi, "INTEGER NOT NULL DEFAULT (unixepoch())")
      .replace(/TIMESTAMP\s+NOT\s+NULL\s+DEFAULT\s+CURRENT_TIMESTAMP/gi, "INTEGER NOT NULL DEFAULT (unixepoch())")
      .replace(/\bsynced_at\s+TIMESTAMP\s+NOT\s+NULL\b/gi, "synced_at INTEGER NOT NULL")
      .replace(/\b(dima_gmt_create|dima_gmt_modified)\s+TIMESTAMP\b/gi, "$1 INTEGER")
      .replace(/\bTIMESTAMP\b/gi, "INTEGER")
      .replace(/CURRENT_TIMESTAMP/gi, "(unixepoch())")
      .replace(/\bCOMMENT\s+'[^']*'/gi, "")
      .replace(/COMMENT\s*=\s*'[^']*'/gi, "")
      .replace(/DECIMAL\(\d+,\s*\d+\)/gi, "INTEGER")
      .replace(/MEDIUMTEXT/gi, "TEXT")
      .replace(/DOUBLE\s+PRECISION/gi, "REAL");

    return extraIndexes.length > 0 ? `${rendered};\n${extraIndexes.join(";\n")}` : rendered;
  },
};

// ── MySQL dialect ──

interface ColumnTypeInfo {
  type: string;
  length?: number;
}

/**
 * Prefix index columns to stay within InnoDB's 767-byte key limit under utf8mb4.
 * VARCHAR(255) * 4 bytes/char = 1020 bytes > 767. We prefix each column to 191 chars
 * (191 * 4 = 764 ≤ 767). For composite indexes, only prefix columns that would fit.
 * Numeric columns are skipped (prefix length on integers is a MySQL syntax error).
 * Short VARCHAR columns (length < 191) are also skipped since they fit in 767 bytes.
 */
function prefixIndexColumns(
  sql: string,
  columnTypes: Map<string, ColumnTypeInfo>,
): string {
  const tableMatch = sql.match(/\bON\s+(\w+)\s+\(/i);
  const table = tableMatch ? tableMatch[1].toLowerCase() : "";
  const numericTypes = new Set([
    "BIGINT", "INT", "INTEGER", "SMALLINT", "TINYINT", "MEDIUMINT",
    "TIMESTAMP", "DATETIME", "DATE", "TIME", "DECIMAL", "NUMERIC",
    "FLOAT", "DOUBLE", "REAL", "BOOLEAN", "BOOL", "BIT",
  ]);

  return sql.replace(
    /\bON\s+(\w+)\s+\(([^)]+)\)/gi,
    (_match, onTable: string, cols: string) => {
      const parts = cols.split(",").map((c: string) => c.trim());
      const prefixed = parts.map((p: string) => {
        // Already has a length suffix like col(191) — leave as-is
        if (/\([^)]*\)/.test(p)) return p;

        const colKey = `${table}.${p.toLowerCase()}`;
        const info = columnTypes.get(colKey);
        const type = info?.type?.toUpperCase() ?? "";

        // Numeric columns: never prefix
        if (numericTypes.has(type)) return p;

        // VARCHAR: only prefix long columns (>=191 chars under utf8mb4)
        if (type === "VARCHAR") {
          const len = info?.length;
          if (len && len >= 191) {
            return `${p}(191)`;
          }
          return p;
        }

        // TEXT variants: must prefix (full TEXT indexing requires a length)
        if (["TEXT", "LONGTEXT", "MEDIUMTEXT", "TINYTEXT"].includes(type)) {
          return `${p}(191)`;
        }

        // Unknown type: be conservative and don't prefix (avoids invalid BIGINT(191) etc.)
        return p;
      });
      return `ON ${onTable} (${prefixed.join(", ")})`;
    },
  );
}

export const mysqlDialect: Dialect = {
  name: "mysql",
  driver: "mysql2",

  quote: (identifier) => `\`${identifier.replace(/`/g, "``")}\``,

  now: () => {
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  },

  epochToDb: (ts) => {
    if (ts === null || ts === undefined) return null;
    const d = new Date(ts * 1000);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  },

  dbToEpoch: (value) => {
    if (value === null || value === undefined) return null;
    if (value instanceof Date) return Math.floor(value.getTime() / 1000);
    if (typeof value === "string") {
      const parsed = Date.parse(value);
      return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
    }
    if (typeof value === "number") {
      // MySQL TIMESTAMP may return a YYYYMMDDHHMMSS number in some client modes.
      if (value > 1e12) {
        const s = String(value);
        const parsed = Date.parse(
          `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}T${s.slice(8, 10)}:${s.slice(10, 12)}:${s.slice(12, 14)}`,
        );
        return Number.isNaN(parsed) ? null : Math.floor(parsed / 1000);
      }
      return value;
    }
    return null;
  },

  typeMapping: {
    autoIncrementPk: "BIGINT PRIMARY KEY AUTO_INCREMENT",
    bigAutoIncrementPk: "BIGINT PRIMARY KEY AUTO_INCREMENT",
    timestamp: "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    timestampNullable: "TIMESTAMP NULL DEFAULT NULL",
    longText: "LONGTEXT",
    decimal: (precision, scale) => `DECIMAL(${precision}, ${scale})`,
    json: () => "JSON",
  },

  supportsReturning: false,
  maxIndexKeyLength: 3072,
  inlineIndexesOnly: false,
  columnTypes: new Map<string, ColumnTypeInfo>(),

  renderDdl(sql: string): string {
    // Record column types from CREATE TABLE so CREATE INDEX can decide whether a
    // prefix length is needed and what length is safe.
    const createTableMatch = sql.match(/^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(([\s\S]+?)\)\s*$/i);
    if (createTableMatch) {
      const table = createTableMatch[1].toLowerCase();
      const body = createTableMatch[2];
      const colDefs = body.split(",").map((s) => s.trim()).filter(Boolean);
      for (const def of colDefs) {
        const colMatch = def.match(/^(\w+)\s+(\w+)(?:\s*\((\d+)\))?/i);
        if (!colMatch) continue;
        const col = colMatch[1].toLowerCase();
        let type = colMatch[2].toUpperCase();
        let len = colMatch[3] ? parseInt(colMatch[3], 10) : undefined;
        // VARCHAR(255) is shortened to VARCHAR(190) later in the chain; record the
        // effective length so CREATE INDEX can decide whether a prefix is needed.
        if (type === "VARCHAR" && len === 255) {
          len = 190;
        }
        this.columnTypes!.set(`${table}.${col}`, { type, length: len });
      }
    }

    // ZDAS/OceanBase parser universally rejects standalone CREATE INDEX (class cast error).
    // For regular MySQL, convert "CREATE INDEX IF NOT EXISTS" → "CREATE INDEX"
    // (MySQL doesn't support IF NOT EXISTS in CREATE INDEX, but does support standalone CREATE INDEX).
    if (/^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s/i.test(sql)) {
      if (this.inlineIndexesOnly) {
        return "SELECT 0 -- ZDAS: standalone CREATE INDEX skipped (inline only)";
      }
      // Strip "IF NOT EXISTS" for MySQL compatibility, then prefix long VARCHAR index columns
      return prefixIndexColumns(
        sql.replace(/CREATE\s+(UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s/i, "CREATE $1INDEX "),
        this.columnTypes!,
      );
    }
    if (this.inlineIndexesOnly && /^\s*CREATE\s+(UNIQUE\s+)?INDEX\s/i.test(sql)) {
      return "SELECT 0 -- ZDAS: standalone CREATE INDEX skipped (inline only)";
    }
    // MySQL doesn't support "DROP INDEX IF EXISTS" — skip these safely.
    // They are cleanup migrations for old indexes that may not exist in fresh installs.
    if (/^\s*DROP\s+INDEX\s+IF\s+EXISTS\s/i.test(sql)) {
      return "SELECT 0 -- MySQL: DROP INDEX IF EXISTS skipped (index may not exist)";
    }
    // SQLite triggers for gmt_modified auto-update are not needed in MySQL —
    // the `ON UPDATE CURRENT_TIMESTAMP` clause in the column definition handles this.
    if (/^\s*CREATE\s+TRIGGER/i.test(sql)) {
      return "SELECT 0 -- MySQL: CREATE TRIGGER skipped (gmt_modified handled by ON UPDATE CURRENT_TIMESTAMP)";
    }
    if (/^\s*DROP\s+TRIGGER/i.test(sql)) {
      return "SELECT 0 -- MySQL: DROP TRIGGER skipped (no triggers in MySQL mode)";
    }
    // SQLite "INSERT OR IGNORE INTO" → MySQL "INSERT IGNORE INTO"
    // (MySQL doesn't support the "OR IGNORE" suffix syntax)
    sql = sql.replace(/INSERT\s+OR\s+IGNORE\s+INTO/gi, "INSERT IGNORE INTO");

    // NOTE: Replacement order matters below. Steps 1-4 must run before step 7
    // because steps 1-4 match the SQLite-specific `(unixepoch())` token which
    // step 7 would otherwise destroy. Similarly, step 9 must run after step 1
    // because it matches the `BIGINT ... AUTO_INCREMENT` produced by step 1.
    return sql
      // 1. INTEGER PK AUTOINCREMENT → BIGINT PK AUTO_INCREMENT
      .replace(/INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT/gi, "BIGINT PRIMARY KEY AUTO_INCREMENT")
      // 2. INTEGER PK AUTO_INCREMENT (canonical variant) → BIGINT PK AUTO_INCREMENT
      .replace(/INTEGER\s+PRIMARY\s+KEY\s+AUTO_INCREMENT/gi, "BIGINT PRIMARY KEY AUTO_INCREMENT")
      // 3. Non-PK BIGINT → BIGINT (identity, keeps MySQL column as BIGINT)
      .replace(/\bBIGINT\b/gi, "BIGINT")
      // 4. gmt_modified: INTEGER DEFAULT (unixepoch()) → TIMESTAMP ON UPDATE
      .replace(
        /gmt_modified\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+\(unixepoch\(\)\)/gi,
        "gmt_modified TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
      )
      // 5. gmt_create: INTEGER DEFAULT (unixepoch()) → TIMESTAMP DEFAULT
      .replace(
        /gmt_create\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+\(unixepoch\(\)\)/gi,
        "gmt_create TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
      )
      // 5a. Bare TIMESTAMP column definitions → add an explicit default to satisfy
      //     MySQL's NO_ZERO_DATE sql_mode.
      //     - TIMESTAMP NOT NULL without DEFAULT → TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
      //     - TIMESTAMP without NULL/NOT NULL/DEFAULT → TIMESTAMP NULL DEFAULT NULL
      //     Already-handled gmt_create/gmt_modified (step 4/5) carry DEFAULT and are skipped.
      .replace(
        /\b(\w+)\s+TIMESTAMP\s+NOT\s+NULL(?!\s+DEFAULT)/gi,
        "$1 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
      )
      .replace(
        /\b(\w+)\s+TIMESTAMP(?!\s+(?:NOT\s+NULL|NULL|DEFAULT))(?=\s*(?:COMMENT|,|\)|$))/gi,
        "$1 TIMESTAMP NULL DEFAULT NULL",
      )
      // 6. dima_gmt_create / dima_gmt_modified: keep as BIGINT (unix epoch from Dima API,
      //    not MySQL-managed timestamps — avoids error 1067 with NO_ZERO_DATE sql_mode)
      .replace(/\b(dima_gmt_create|dima_gmt_modified)\s+INTEGER\b/gi, "$1 BIGINT")
      // 7. synced_at: INTEGER NOT NULL → BIGINT NOT NULL (unix epoch, not a TIMESTAMP)
      .replace(/\bsynced_at\s+INTEGER\s+NOT\s+NULL\b/gi, "synced_at BIGINT NOT NULL")
      // 8. Remaining INTEGER columns with DEFAULT (unixepoch()) that aren't gmt_create/gmt_modified
      //    (e.g. first_seen, last_seen, started_at) → BIGINT DEFAULT 0
      //    (must run before step 9 to avoid invalid `INTEGER DEFAULT CURRENT_TIMESTAMP`)
      .replace(/(\w+)\s+INTEGER\s+NOT\s+NULL\s+DEFAULT\s+\(unixepoch\(\)\)/gi, "$1 BIGINT NOT NULL DEFAULT 0")
      // 9. Remaining (unixepoch()) → CURRENT_TIMESTAMP (must run after steps 4-5)
      .replace(/\(unixepoch\(\)\)/gi, "CURRENT_TIMESTAMP")
      // 9. REAL → DECIMAL(20,6)
      .replace(/\bREAL\b/gi, "DECIMAL(20,6)")
      // 10. VARCHAR(255) → VARCHAR(190): avoids InnoDB 767-byte key limit so
      //     unique indexes on these columns (often used as FK targets) can be
      //     full-column instead of prefix indexes, which MySQL requires for FKs.
      .replace(/\bVARCHAR\s*\(\s*255\s*\)/gi, "VARCHAR(190)")
      // 11. id BIGINT PK AUTO_INCREMENT → add COMMENT '主键ID' when the
      //     canonical DDL has not already supplied one (must run after step 1).
      .replace(
        /\bid\s+(BIGINT(?:\s*\(\d+\))?\s+PRIMARY\s+KEY\s+AUTO_INCREMENT)(?!\s+COMMENT\b)/gi,
        "id $1 COMMENT '主键ID'",
      )
      // 12. Remaining INTEGER → BIGINT (SQLite INTEGER is 64-bit; MySQL INT is 32-bit).
      //     Must run after all preceding steps so PK/FK types match.
      .replace(/\bINTEGER\b/gi, "BIGINT");
  },
};

// ── ZDAS dialect ──

/**
 * ZDAS is MySQL-compatible (OceanBase protocol) but has stricter index and
 * prepared-statement constraints. It reuses the MySQL dialect for type
 * mappings and timestamp handling but overrides connection/runtime flags.
 */
export const zdasDialect: Dialect = {
  ...mysqlDialect,
  name: "zdas",
  inlineIndexesOnly: true,
  maxIndexKeyLength: 767,
  // renderDdl inherited from mysqlDialect — ZDAS is MySQL-compatible for DDL.
};

export function dialectFor(dbType: "sqlite" | "mysql" | "zdas" | "noop"): Dialect {
  switch (dbType) {
    case "sqlite":
    case "noop":
      // NoOp databases execute no SQL; sqliteDialect is a safe neutral default.
      return sqliteDialect;
    case "mysql":
      return mysqlDialect;
    case "zdas":
      return zdasDialect;
    default:
      // Exhaustiveness guard; should never happen at runtime.
      throw new Error(`Unsupported database type: ${String(dbType)}`);
  }
}
