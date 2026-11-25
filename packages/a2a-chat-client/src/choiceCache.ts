/**
 * 传输选择缓存（spec stage2 §2/§3.3）。页内会话级 Map——不持久化，
 * 页面刷新即天然重探。probe-v3 可由后续探测翻案；locked-v3 由运行期
 * 硬失败设置（§3.3 窄定义：传输面不兼容/鉴权持续拒绝），本页面会话内
 * 最高优先级，自动清除无效。
 */
export type EntryKind = 'a2a' | 'probe-v3' | 'locked-v3';
export interface ChoiceEntry { kind: EntryKind; at: number }

export class TransportChoiceCache {
  private readonly map = new Map<string, ChoiceEntry>();

  private key(sessionKey: string, endpointBase: string): string {
    // endpoint 在前——防 sessionKey 跨 endpoint 复用时的键位歧义（review
    // 教训：不裸用 sessionKey 做缓存身份）
    return `${endpointBase}::${sessionKey}`;
  }

  get(sessionKey: string, endpointBase: string): ChoiceEntry | undefined {
    return this.map.get(this.key(sessionKey, endpointBase));
  }

  set(sessionKey: string, endpointBase: string, kind: EntryKind): ChoiceEntry {
    const entry: ChoiceEntry = { kind, at: Date.now() };
    this.map.set(this.key(sessionKey, endpointBase), entry);
    return entry;
  }

  /** 探测收敛（locked 优先——已 locked 的会话探测结果不翻案）。 */
  decide(sessionKey: string, endpointBase: string, probeOk: boolean): ChoiceEntry {
    const existing = this.get(sessionKey, endpointBase);
    if (existing?.kind === 'locked-v3') return existing;
    return this.set(sessionKey, endpointBase, probeOk ? 'a2a' : 'probe-v3');
  }

  /** 运行期硬失败锁定（spec §3.3）。 */
  markLocked(sessionKey: string, endpointBase: string): ChoiceEntry {
    return this.set(sessionKey, endpointBase, 'locked-v3');
  }

  clear(): void { this.map.clear(); }
}