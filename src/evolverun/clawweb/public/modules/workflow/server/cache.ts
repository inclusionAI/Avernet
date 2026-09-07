export interface ApiCacheOptions {
  ttlMs: number;
  maxSize?: number;
  keyPrefix: string;
}

export class ApiCache<T> {
  private cache = new Map<string, { data: T; expireAt: number }>();
  private readonly opts: Required<ApiCacheOptions>;

  constructor(opts: ApiCacheOptions) {
    this.opts = { maxSize: 200, ...opts };
  }

  get(key: string): T | undefined {
    const entry = this.cache.get(key);
    if (!entry) return undefined;
    if (entry.expireAt <= Date.now()) {
      this.cache.delete(key);
      return undefined;
    }
    return entry.data;
  }

  set(key: string, data: T): void {
    if (this.cache.size >= this.opts.maxSize) {
      const firstKey = this.cache.keys().next().value;
      if (firstKey !== undefined) this.cache.delete(firstKey);
    }
    this.cache.set(key, { data, expireAt: Date.now() + this.opts.ttlMs });
  }

  invalidate(key?: string): void {
    if (key) {
      this.cache.delete(key);
    } else {
      this.cache.clear();
    }
  }
}