import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import type { SessionBinding, SessionHistoryMessage } from './types.js';

const DEFAULT_WRITE_DEBOUNCE_MS = 50;

export type SessionStoreOptions = {
  writeDebounceMs?: number;
};

export class SessionStore {
  private filePath: string;
  private byAcpSessionId = new Map<string, SessionBinding>();
  private byGatewaySessionKey = new Map<string, SessionBinding>();
  private writeDebounceMs: number;
  private dirty = false;
  private pendingTimer: NodeJS.Timeout | null = null;
  private writeInFlight: Promise<void> | null = null;
  private queuedWrite: Promise<void> | null = null;

  constructor(filePath: string, options: SessionStoreOptions = {}) {
    this.filePath = filePath;
    this.writeDebounceMs = options.writeDebounceMs ?? DEFAULT_WRITE_DEBOUNCE_MS;
    this.load();
  }

  private load() {
    if (!fs.existsSync(this.filePath)) return;
    const raw = fs.readFileSync(this.filePath, 'utf8');
    const items = JSON.parse(raw) as SessionBinding[];
    for (const item of items) {
      this.byAcpSessionId.set(item.acpSessionId, item);
      this.byGatewaySessionKey.set(item.gatewaySessionKey, item);
    }
  }

  private scheduleSave() {
    this.dirty = true;
    if (this.pendingTimer) return;
    this.pendingTimer = setTimeout(() => {
      this.pendingTimer = null;
      void this.writeNow();
    }, this.writeDebounceMs);
  }

  private writeNow(): Promise<void> {
    if (this.writeInFlight) {
      // A write is already running — queue another flush after it
      if (!this.queuedWrite) {
        this.queuedWrite = this.writeInFlight.then(() => {
          this.queuedWrite = null;
          if (this.dirty) return this.writeNow();
        });
      }
      return this.queuedWrite;
    }
    if (!this.dirty) return Promise.resolve();
    this.dirty = false;
    const snapshot = JSON.stringify([ ...this.byAcpSessionId.values() ], null, 2);
    this.writeInFlight = this.writeAtomic(snapshot).finally(() => {
      this.writeInFlight = null;
    });
    return this.writeInFlight;
  }

  private async writeAtomic(contents: string): Promise<void> {
    const dir = path.dirname(this.filePath);
    await fsp.mkdir(dir, { recursive: true });
    const tmp = `${this.filePath}.${process.pid}.${Date.now()}.tmp`;
    await fsp.writeFile(tmp, contents, 'utf8');
    await fsp.rename(tmp, this.filePath);
  }

  /** Await any pending writes. Use on shutdown or from tests. */
  async flush(): Promise<void> {
    if (this.pendingTimer) {
      clearTimeout(this.pendingTimer);
      this.pendingTimer = null;
    }
    await this.writeNow();
    if (this.queuedWrite) await this.queuedWrite;
  }

  get(acpSessionId: string) {
    return this.byAcpSessionId.get(acpSessionId);
  }

  getByGatewaySessionKey(gatewaySessionKey: string) {
    return this.byGatewaySessionKey.get(gatewaySessionKey);
  }

  /**
   * 查找 binding：精确匹配优先，失败后双向子串回退。
   * 场景1: inject 传短 key "uuid:hash"，store 存完整 key → key.includes(query)
   * 场景2: chat.send 传完整 key，store 存短 key（inject 先创建）→ query.includes(key)
   */
  findBySessionKey(sessionKey: string): SessionBinding | undefined {
    const exact = this.byGatewaySessionKey.get(sessionKey);
    if (exact) return exact;
    for (const [ key, binding ] of this.byGatewaySessionKey) {
      if (key.includes(sessionKey) || sessionKey.includes(key)) return binding;
    }
    return undefined;
  }

  set(binding: SessionBinding) {
    if (!binding.history) binding.history = [];
    const existing = this.byAcpSessionId.get(binding.acpSessionId);
    if (existing && existing.gatewaySessionKey !== binding.gatewaySessionKey) {
      this.byGatewaySessionKey.delete(existing.gatewaySessionKey);
    }
    this.byAcpSessionId.set(binding.acpSessionId, binding);
    this.byGatewaySessionKey.set(binding.gatewaySessionKey, binding);
    this.scheduleSave();
  }

  appendHistory(gatewaySessionKey: string, message: SessionHistoryMessage) {
    const binding = this.byGatewaySessionKey.get(gatewaySessionKey);
    if (!binding) return;
    binding.history = binding.history ?? [];
    binding.history.push(message);
    binding.updatedAt = message.timestamp;
    this.scheduleSave();
  }

  deleteByGatewaySessionKey(gatewaySessionKey: string) {
    const binding = this.byGatewaySessionKey.get(gatewaySessionKey);
    if (!binding) return;
    this.byGatewaySessionKey.delete(gatewaySessionKey);
    this.byAcpSessionId.delete(binding.acpSessionId);
    this.scheduleSave();
  }

  list() {
    return [ ...this.byAcpSessionId.values() ].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }
}
