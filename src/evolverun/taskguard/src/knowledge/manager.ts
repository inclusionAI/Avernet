/**
 * KnowledgeBaseManager — loads enabled KB configs from DB and creates adapters.
 *
 * Caches config rows and refreshes on a configurable TTL so running workflows
 * pick up additions/changes without restart.
 */
import type { IDatabase } from "../db/types.js";
import type { KnowledgeBase } from "./types.js";
import { GrtKbAdapter, type GrtKbConfig } from "./grt-kb-adapter.js";

/** Row shape from the knowledge_bases table. */
type KbRow = {
  kb_id: string;
  name: string;
  instance_name: string;
  interface_name: string;
  token: string;
  user_name: string;
  user_id: string;
  top_k: number;
  ranking_threshold: number;
  vector_threshold: number;
  ranking_model: string;
  env: string;
};

const DEFAULT_CACHE_TTL_MS = 60_000;
const ENV_CACHE_TTL_KEY = "KB_CACHE_TTL_MS";

/** Cache entry holding adapter instances keyed by kb_id. */
type CacheEntry = {
  adapters: Map<string, KnowledgeBase>;
  timestamp: number;
};

/**
 * KnowledgeBaseManager provides KB adapters by kb_id.
 *
 * It queries `knowledge_bases` for enabled rows, creates a GrtKbAdapter for
 * each, and caches them. Call `refresh()` or `startAutoRefresh()` to update
 * the cache from DB.
 */
export class KnowledgeBaseManager {
  private readonly db: IDatabase;
  private readonly cacheTtlMs: number;
  private cache: CacheEntry | null = null;
  private refreshTimer: ReturnType<typeof setInterval> | null = null;

  constructor(db: IDatabase, cacheTtlMs?: number) {
    this.db = db;
    this.cacheTtlMs =
      cacheTtlMs ??
      (process.env[ENV_CACHE_TTL_KEY]
        ? Number(process.env[ENV_CACHE_TTL_KEY])
        : DEFAULT_CACHE_TTL_MS);
  }

  /** Get a single KB adapter by kb_id. Returns undefined if not found. */
  async getById(kbId: string): Promise<KnowledgeBase | undefined> {
    const adapters = await this.getAdapters();
    return adapters.get(kbId);
  }

  /** Get all currently cached KB adapters. */
  async getAll(): Promise<KnowledgeBase[]> {
    const adapters = await this.getAdapters();
    return [...adapters.values()];
  }

  /** Force a cache refresh from DB. */
  async refresh(): Promise<void> {
    this.cache = await this.loadFromDb();
  }

  /** Start periodic auto-refresh. Call once at startup. */
  startAutoRefresh(intervalMs?: number): void {
    const interval = intervalMs ?? this.cacheTtlMs;
    this.stopAutoRefresh();
    this.refresh().catch(() => { /* best-effort */ });
    this.refreshTimer = setInterval(() => {
      this.refresh().catch(() => { /* best-effort */ });
    }, interval);
    this.refreshTimer.unref?.();
  }

  /** Stop periodic auto-refresh. */
  stopAutoRefresh(): void {
    if (this.refreshTimer) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  // ── Private ──

  private async getAdapters(): Promise<Map<string, KnowledgeBase>> {
    if (this.cache && Date.now() - this.cache.timestamp < this.cacheTtlMs) {
      return this.cache.adapters;
    }
    this.cache = await this.loadFromDb();
    return this.cache.adapters;
  }

  private async loadFromDb(): Promise<CacheEntry> {
    const adapters = new Map<string, KnowledgeBase>();
    try {
      const rows = await this.db.query<KbRow>(
        `SELECT kb_id, name, instance_name, interface_name, token,
                user_name, user_id, top_k, ranking_threshold,
                vector_threshold, ranking_model, env
         FROM knowledge_bases
         WHERE enabled = 1
         ORDER BY kb_id`,
      );
      for (const row of rows) {
        const config: GrtKbConfig = {
          kbId: row.kb_id,
          name: row.name,
          instanceName: row.instance_name,
          interfaceName: row.interface_name,
          token: row.token,
          userName: row.user_name,
          userId: row.user_id,
          topK: row.top_k,
          rankingThreshold: row.ranking_threshold,
          vectorThreshold: row.vector_threshold,
          rankingModel: row.ranking_model,
          env: row.env,
        };
        adapters.set(row.kb_id, new GrtKbAdapter(config));
      }
    } catch {
      // Table may not exist yet or DB unavailable — return empty cache
    }
    return { adapters, timestamp: Date.now() };
  }
}
