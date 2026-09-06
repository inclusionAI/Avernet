/**
 * AntLogs Direct API Collector — queries AntLogs OpenAPI directly
 * using Hawk authentication, bypassing BaaS Bot and MCP Gateway.
 *
 * API docs: https://yuque.antfin.com/ant-dts/antlogs-user-guide/openapi-scenario-search
 * Auth docs: https://yuque.antfin.com/ant-dts/antlogs-user-guide/openapi-auth
 */
// @ts-expect-error — hawk v9 has no type declarations
import hawk from "hawk";

/* ── Types ──────────────────────────────────────────────────────── */

export interface AntLogsSource {
  name: string;
  region: string;
  app: string;
  tenant: string;
  /** Per-source baseUrl override (e.g. antlogs.alipay.com for BCN/secbaas) */
  baseUrl?: string;
  defaultLogstore?: string;
  defaultQuery?: string;
  defaultEnabled?: boolean;
}

export interface AntLogsConfig {
  apiId: string;
  apiKey: string;
  baseUrl: string;
  requestTimeoutMs: number;
  maxBatonRounds: number;
  sources: AntLogsSource[];
}

export interface LogEntry {
  timestamp: string;
  level: "ERROR" | "WARN" | "INFO" | "DEBUG";
  message: string;
  source: string;
  /** Optional structured metadata (e.g. traceId, raw fields from AntLogs) */
  metadata?: Record<string, unknown>;
  /** Raw log line */
  raw?: string;
}

export interface SourceResult {
  source: AntLogsSource;
  status: "success" | "partial" | "failed";
  entries: LogEntry[];
  totalAvailable: number;
  entriesCount: number;
  errorEntriesCount: number;
  durationMs: number;
  error?: string;
  batonRounds: number;
}

export interface AntLogsCollectResult {
  entries: LogEntry[];
  sourceResults: SourceResult[];
  totalEntries: number;
  totalErrors: number;
  durationMs: number;
  allSourcesSucceeded: boolean;
  collectorType: "antlogs";
}

/* ── AntLogs API response types ─────────────────────────────────── */

interface AntLogsLogstore {
  logstoreName: string;
  projectName: string;
  fullPath: string;
  hasSlsIndex: boolean;
}

interface AntLogsColumn {
  name: string;
  type: string;
}

interface AntLogsQueryResponse {
  success: boolean;
  data?: {
    response: {
      queryId: string;
      status: number; // 1=running, 3=finished
      columns: AntLogsColumn[];
      rows: Array<Record<string, unknown>>;
      errorMessage: string | null;
    };
    baton: boolean;
    progress: number;
    runtime?: string;
    incomplete?: boolean;
  };
  errorMessage?: string;
  errorCode?: string;
}

/* ── Collector ──────────────────────────────────────────────────── */

export class AntLogsCollector {
  private readonly config: AntLogsConfig;
  private readonly logstoreCache = new Map<string, string[]>();

  constructor(config: AntLogsConfig) {
    this.config = config;
  }

  /**
   * Collect logs from selected sources in parallel.
   * @param params.lookbackMinutes - How far back to query
   * @param params.sources - Source names to query (undefined = defaultEnabled)
   * @param params.minSeverity - Minimum severity for LogQL construction
   */
  async collect(params: {
    lookbackMinutes: number;
    sources?: string[];
    minSeverity?: string;
  }): Promise<AntLogsCollectResult> {
    const selectedSources = this.resolveSources(params.sources);
    const end = Math.floor(Date.now() / 1000);
    const begin = end - params.lookbackMinutes * 60;
    const logQL = this.buildLogQL(params.minSeverity);

    const startTime = Date.now();

    // Query all sources in parallel
    const results = await Promise.allSettled(
      selectedSources.map((source) =>
        this.querySource(source, begin, end, logQL)
      )
    );

    const sourceResults: SourceResult[] = results.map((r, i) => {
      if (r.status === "fulfilled") return r.value;
      return {
        source: selectedSources[i],
        status: "failed" as const,
        entries: [],
        totalAvailable: 0,
        entriesCount: 0,
        errorEntriesCount: 0,
        durationMs: 0,
        error: r.status === "rejected" ? String(r.reason) : "Unknown error",
        batonRounds: 0,
      };
    });

    const allEntries = sourceResults.flatMap((sr) => sr.entries);
    const totalErrors = sourceResults.reduce(
      (sum, sr) => sum + sr.errorEntriesCount,
      0
    );
    const maxDuration = Math.max(...sourceResults.map((sr) => sr.durationMs), Date.now() - startTime);
    const allSucceeded = sourceResults.every(
      (sr) => sr.status === "success" || sr.status === "partial"
    );

    return {
      entries: allEntries,
      sourceResults,
      totalEntries: allEntries.length,
      totalErrors,
      durationMs: maxDuration,
      allSourcesSucceeded: allSucceeded,
      collectorType: "antlogs",
    };
  }

  /* ── Source resolution ──────────────────────────────────────── */

  private resolveSources(names?: string[]): AntLogsSource[] {
    if (names && names.length > 0) {
      return names
        .map((name) => this.config.sources.find((s) => s.name === name))
        .filter((s): s is AntLogsSource => s !== undefined);
    }
    return this.config.sources.filter((s) => s.defaultEnabled !== false);
  }

  /* ── Single source query ────────────────────────────────────── */

  private async querySource(
    source: AntLogsSource,
    begin: number,
    end: number,
    logQL: string
  ): Promise<SourceResult> {
    const startTime = Date.now();

    try {
      // 1. Resolve logstore(s)
      const logstores = await this.resolveLogstores(source);
      if (logstores.length === 0) {
        return {
          source,
          status: "failed",
          entries: [],
          totalAvailable: 0,
          entriesCount: 0,
          errorEntriesCount: 0,
          durationMs: Date.now() - startTime,
          error: "No logstores found",
          batonRounds: 0,
        };
      }

      // 2. Pick the best logstore — prefer error logstore, then first available
      const logstore = this.pickLogstore(logstores, source);
      console.log(`[antlogs] Source "${source.name}": ${logstores.length} logstores found, selected: ${logstore}`);

      // 3. Build query — per-source defaultQuery takes priority over generic LogQL
      const query = source.defaultQuery ?? logQL;
      console.log(`[antlogs] Source "${source.name}": query="${query}", timeRange=${begin}-${end}`);

      // 4. Query with baton pagination (using per-source baseUrl)
      const queryResult = await this.paginateQuery(
        source,
        logstore,
        begin,
        end,
        query
      );

      // 5. Map rows to LogEntry
      const entries = this.mapRowsToEntries(
        queryResult.rows,
        queryResult.columns,
        source.name
      );

      const errorCount = entries.filter((e) =>
        /error|fatal|exception/i.test(e.level)
      ).length;

      return {
        source,
        status: queryResult.truncated ? "partial" : "success",
        entries,
        totalAvailable: queryResult.totalAvailable,
        entriesCount: entries.length,
        errorEntriesCount: errorCount,
        durationMs: Date.now() - startTime,
        batonRounds: queryResult.batonRounds,
      };
    } catch (err) {
      return {
        source,
        status: "failed",
        entries: [],
        totalAvailable: 0,
        entriesCount: 0,
        errorEntriesCount: 0,
        durationMs: Date.now() - startTime,
        error: err instanceof Error ? err.message : String(err),
        batonRounds: 0,
      };
    }
  }

  /* ── Per-source URL resolution ──────────────────────────────── */

  /** Resolve the base URL for a source — source-specific override takes priority */
  private resolveBaseUrl(source: AntLogsSource): string {
    return source.baseUrl ?? this.config.baseUrl;
  }

  /* ── Logstore discovery ─────────────────────────────────────── */

  /**
   * Pick the best logstore from the discovered list.
   *
   * Matching priority:
   * 1. Exact match of defaultLogstore
   * 2. Partial match: defaultLogstore appears anywhere in the logstore name
   * 3. Application runtime logs — "start", "app", "runtime" in name
   *    (These contain structured ERROR/INFO/WARN from the application)
   * 4. Error logstore — "error" in name (usually Nginx-level, less useful)
   * 5. Any non-access logstore
   * 6. First logstore
   */
  private pickLogstore(logstores: string[], source: AntLogsSource): string {
    // 1. Exact match
    if (source.defaultLogstore && logstores.includes(source.defaultLogstore)) {
      return source.defaultLogstore;
    }
    // 2. Partial match — defaultLogstore substring in logstore name
    if (source.defaultLogstore) {
      const partial = logstores.find((l) => l.includes(source.defaultLogstore!));
      if (partial) return partial;
    }
    // 3. Application runtime logs (start-log, app-log, runtime, etc.)
    const appStore = logstores.find((l) => /start|app|runtime/i.test(l) && !/access/i.test(l));
    if (appStore) return appStore;
    // 4. Error logstore (usually Nginx-level — less useful but better than nothing)
    const errorStore = logstores.find((l) => /error/i.test(l) && !/access/i.test(l));
    if (errorStore) return errorStore;
    // 5. Any non-access logstore
    const nonAccess = logstores.find((l) => !/access/i.test(l));
    if (nonAccess) return nonAccess;
    // 6. Fallback
    return logstores[0];
  }

  private async resolveLogstores(source: AntLogsSource): Promise<string[]> {
    const cacheKey = `${source.region}:${source.app}`;
    if (this.logstoreCache.has(cacheKey)) {
      return this.logstoreCache.get(cacheKey)!;
    }

    try {
      const baseUrl = this.resolveBaseUrl(source);
      const url = `${baseUrl}/openapi/v1/dtm/regions/${source.region}/apps/${source.app}/logs`;
      const body = await this.makeRequest(url);

      if (!body.success || !Array.isArray(body.data)) {
        return [];
      }

      const logstores = (body.data as AntLogsLogstore[]).map(
        (l) => l.logstoreName
      );
      this.logstoreCache.set(cacheKey, logstores);
      return logstores;
    } catch {
      return [];
    }
  }

  /* ── Log query with baton pagination ───────────────────────── */

  private async paginateQuery(
    source: AntLogsSource,
    logstore: string,
    begin: number,
    end: number,
    query: string
  ): Promise<{
    rows: Array<Record<string, unknown>>;
    columns: AntLogsColumn[];
    totalAvailable: number;
    batonRounds: number;
    truncated: boolean;
  }> {
    const allRows: Array<Record<string, unknown>> = [];
    let columns: AntLogsColumn[] = [];
    let batonRounds = 0;
    let batonParam: string | undefined;
    let totalAvailable = 0;
    let truncated = false;

    do {
      const queryParams: Record<string, string> = {
        begin: String(begin),
        end: String(end),
        query,
        limit: "500",
        reverse: "true",
      };
      if (batonParam) {
        queryParams.baton = batonParam;
      }

      const queryString = new URLSearchParams(queryParams).toString();
      const baseUrl = this.resolveBaseUrl(source);
      const url = `${baseUrl}/openapi/v1/dtm/regions/${source.region}/apps/${source.app}/logs/${encodeURIComponent(logstore)}/query?${queryString}`;

      const body = await this.makeRequest(url);

      if (!body.success || !body.data) {
        console.warn(`[antlogs] Query failed for source "${source.name}": success=${body.success}, errorMessage=${body.errorMessage ?? "none"}, errorCode=${body.errorCode ?? "none"}`);
        throw new Error(
          `AntLogs query failed: ${body.errorMessage ?? body.errorCode ?? "unknown error"}`
        );
      }

      const resp = body.data.response;
      columns = resp.columns ?? [];
      const rows = resp.rows ?? [];
      console.log(`[antlogs] Source "${source.name}": query returned ${rows.length} rows, baton=${body.data.baton}, status=${resp.status}, progress=${body.data.progress}`);
      allRows.push(...rows);
      totalAvailable += rows.length;
      batonRounds++;

      if (body.data.baton) {
        batonParam = resp.queryId;
        truncated = true;
      } else {
        batonParam = undefined;
        truncated = false;
      }
    } while (batonParam && batonRounds < this.config.maxBatonRounds);

    return {
      rows: allRows,
      columns,
      totalAvailable,
      batonRounds,
      truncated,
    };
  }

  /* ── Row mapping ───────────────────────────────────────────── */

  private mapRowsToEntries(
    rows: Array<Record<string, unknown>>,
    _columns: AntLogsColumn[],
    sourceName: string
  ): LogEntry[] {
    return rows.map((row) => {
      // AntLogs rows with map-type columns contain an `items` array of key-value pairs
      const items = row.items as
        | Array<{ [key: string]: string }>
        | undefined;
      if (!items || !Array.isArray(items) || items.length === 0) {
        const content = String(row.content ?? row.message ?? "");
        return {
          timestamp: String(row.__time__ ?? ""),
          level: this.detectLevel(content),
          message: content,
          source: sourceName,
          raw: JSON.stringify(row),
        };
      }

      // Flatten items array into a map
      const flat: Record<string, string> = {};
      for (const item of items) {
        for (const [k, v] of Object.entries(item)) {
          flat[k] = v;
        }
      }

      const content = flat.content ?? flat.msg ?? flat.message ?? "";
      const traceId = flat.traceId ?? flat.trace_id ?? flat.requestId;
      return {
        timestamp: flat.__time__ ?? "",
        level: this.detectLevel(content),
        message: content,
        source: sourceName,
        metadata: { ...flat, ...(traceId ? { traceId } : {}) },
        raw: content,
      };
    });
  }

  /**
   * Detect log level from log content.
   *
   * Priority: structured log prefix (e.g. " - ERROR - ", " - WARNING - ", " [error] ")
   * beats unstructured keywords to avoid false positives from words like "error"
   * appearing inside JSON bodies or URLs.
   */
  private detectLevel(content: string): "ERROR" | "WARN" | "INFO" | "DEBUG" {
    // ── Structured patterns (high confidence) ──
    // Python/Java log format: " - ERROR - ", " - WARNING - ", " - INFO - "
    if (/\b(?:FATAL|CRITICAL)\b/.test(content) || / - (?:FATAL|CRITICAL) - /i.test(content)) return "ERROR";
    if (/\bERROR\b/.test(content) && / - ERROR - /i.test(content)) return "ERROR";
    if (/\bWARNING\b/.test(content) && / - WARNING - /i.test(content)) return "WARN";
    if (/\bINFO\b/.test(content) && / - INFO - /i.test(content)) return "INFO";
    if (/\bDEBUG\b/.test(content) && / - DEBUG - /i.test(content)) return "DEBUG";

    // ── Bracket patterns (medium confidence) ──
    if (/\[(?:error|fatal|critical)\]/i.test(content)) return "ERROR";
    if (/\[(?:warn|warning)\]/i.test(content)) return "WARN";
    if (/\[info\]/i.test(content)) return "INFO";
    if (/\[debug\]/i.test(content)) return "DEBUG";

    // ── Prefix patterns (medium confidence) ──
    // Log4j/slf4j style: "ERROR ", "WARN " at start of line
    if (/^(?:FATAL|ERROR)\b/i.test(content)) return "ERROR";
    if (/^WARN(?:ING)?\b/i.test(content)) return "WARN";
    if (/^INFO\b/i.test(content)) return "INFO";
    if (/^DEBUG\b/i.test(content)) return "DEBUG";

    // ── HTTP status codes (low confidence - only real errors) ──
    if (/\bHTTP error:\s*[45]\d{2}\b/i.test(content)) return "ERROR";

    // Default: treat as INFO — don't flag keywords inside content bodies
    return "INFO";
  }

  /* ── Keyword search ────────────────────────────────────── */

  /**
   * Search logs from selected sources using a keyword query.
   * Unlike `collect()` which uses `*` as LogQL, this method embeds the
   * keyword into the AntLogs query so the server filters before returning,
   * significantly reducing result size for interactive log search.
   *
   * @param params.keyword  - Free-text keyword to search for in log content
   * @param params.sources  - Source names to query (undefined = defaultEnabled)
   * @param params.from     - Start time in epoch seconds
   * @param params.to       - End time in epoch seconds
   * @param params.limit    - Max rows per source (default 200, max 500)
   */
  async search(params: {
    keyword: string;
    sources?: string[];
    from: number;
    to: number;
    limit?: number;
    /** Disable raw query logging for privacy-sensitive callers such as Repair. */
    suppressQueryLog?: boolean;
  }): Promise<AntLogsCollectResult> {
    const selectedSources = this.resolveSources(params.sources);
    const query = params.keyword.trim() || "*";
    const perSourceLimit = Math.min(params.limit ?? 200, 500);

    const startTime = Date.now();

    const results = await Promise.allSettled(
      selectedSources.map((source) =>
        this.searchSource(
          source,
          params.from,
          params.to,
          query,
          perSourceLimit,
          params.suppressQueryLog === true,
        )
      )
    );

    const sourceResults: SourceResult[] = results.map((r, i) => {
      if (r.status === "fulfilled") return r.value;
      return {
        source: selectedSources[i],
        status: "failed" as const,
        entries: [],
        totalAvailable: 0,
        entriesCount: 0,
        errorEntriesCount: 0,
        durationMs: 0,
        error: r.status === "rejected" ? String(r.reason) : "Unknown error",
        batonRounds: 0,
      };
    });

    const allEntries = sourceResults.flatMap((sr) => sr.entries);
    const totalErrors = sourceResults.reduce(
      (sum, sr) => sum + sr.errorEntriesCount,
      0
    );
    const maxDuration = Math.max(...sourceResults.map((sr) => sr.durationMs), Date.now() - startTime);
    const allSucceeded = sourceResults.every(
      (sr) => sr.status === "success" || sr.status === "partial"
    );

    return {
      entries: allEntries,
      sourceResults,
      totalEntries: allEntries.length,
      totalErrors,
      durationMs: maxDuration,
      allSourcesSucceeded: allSucceeded,
      collectorType: "antlogs",
    };
  }

  /* ── Single-source keyword search ───────────────────────── */

  private async searchSource(
    source: AntLogsSource,
    begin: number,
    end: number,
    query: string,
    limit: number,
    suppressQueryLog: boolean,
  ): Promise<SourceResult> {
    const startTime = Date.now();

    try {
      const logstores = await this.resolveLogstores(source);
      if (logstores.length === 0) {
        return {
          source,
          status: "failed",
          entries: [],
          totalAvailable: 0,
          entriesCount: 0,
          errorEntriesCount: 0,
          durationMs: Date.now() - startTime,
          error: "No logstores found",
          batonRounds: 0,
        };
      }

      const logstore = this.pickLogstore(logstores, source);
      if (suppressQueryLog) {
        console.log(`[antlogs] Search "${source.name}": logstore=${logstore}, query=[REDACTED], time=${begin}-${end}`);
      } else {
        console.log(`[antlogs] Search "${source.name}": logstore=${logstore}, query="${query}", time=${begin}-${end}`);
      }

      // Use paginateQuery with the keyword as query instead of "*"
      const queryResult = await this.paginateQuery(
        source,
        logstore,
        begin,
        end,
        query
      );

      const allEntries = this.mapRowsToEntries(
        queryResult.rows,
        queryResult.columns,
        source.name
      );

      // Apply client-requested limit
      const entries = limit > 0 ? allEntries.slice(0, limit) : allEntries;

      const errorCount = entries.filter((e) =>
        /error|fatal|exception/i.test(e.level)
      ).length;

      return {
        source,
        status: queryResult.truncated ? "partial" : "success",
        entries,
        totalAvailable: queryResult.totalAvailable,
        entriesCount: entries.length,
        errorEntriesCount: errorCount,
        durationMs: Date.now() - startTime,
        batonRounds: queryResult.batonRounds,
      };
    } catch (err) {
      return {
        source,
        status: "failed",
        entries: [],
        totalAvailable: 0,
        entriesCount: 0,
        errorEntriesCount: 0,
        durationMs: Date.now() - startTime,
        error: err instanceof Error ? err.message : String(err),
        batonRounds: 0,
      };
    }
  }

  /* ── LogQL construction ────────────────────────────────────── */

  /**
   * Build LogQL query string for AntLogs.
   *
   * Most AntLogs logstores store raw log lines without structured `level` fields,
   * so `level:ERROR` returns zero results. Instead, we use wildcard `"*"` and
   * rely on post-query level detection via `detectLevel()`.
   *
   * Per-source `defaultQuery` in the config overrides this entirely.
   */
  private buildLogQL(_minSeverity?: string): string {
    // Always use wildcard — level filtering happens post-query via detectLevel()
    return "*";
  }

  /* ── HTTP request with Hawk auth ───────────────────────────── */

  private async makeRequest(url: string): Promise<AntLogsQueryResponse> {
    const authHeader = this.generateHawkHeader(url, "GET");

    const controller = new AbortController();
    const timeout = setTimeout(
      () => controller.abort(),
      this.config.requestTimeoutMs
    );

    try {
      const response = await fetch(url, {
        method: "GET",
        headers: {
          Authorization: authHeader,
          Accept: "application/json",
        },
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      return (await response.json()) as AntLogsQueryResponse;
    } finally {
      clearTimeout(timeout);
    }
  }

  private generateHawkHeader(url: string, method: string): string {
    const result = hawk.client.header(url, method, {
      credentials: {
        id: this.config.apiId,
        key: this.config.apiKey,
        algorithm: "sha256",
      },
    });
    return result.header;
  }
}
