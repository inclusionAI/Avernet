import { MonitoringError, type MonitoringTarget } from './contracts.js';
import type { DirectoryScope, LegacyReportBinding, MonitoringBotDirectory, MonitoringTargetResolver } from './directory-contracts.js';
import { target, targetKey } from './target.js';
import { id } from './validation.js';

/** Scoped to a single trusted tenant/storage and audited target environments, never a UI principal. */
export function createTargetResolver(directory: MonitoringBotDirectory, scope: DirectoryScope,
  bindings: readonly LegacyReportBinding[] = []): MonitoringTargetResolver {
  const byWire = new Map<string, MonitoringTarget>(), byTarget = new Map<string, string>();
  for (const binding of bindings) {
    const wire = id(binding.reportedBotId), resolved = target(binding.target), key = targetKey(resolved);
    if (byWire.has(wire) || byTarget.has(key) || !scope.allowedTargetEnvs.includes(resolved.env)) {
      throw new MonitoringError('TARGET_BINDING_CONFLICT', '监控目标绑定重复或不在允许范围内。');
    }
    byWire.set(wire, resolved); byTarget.set(key, wire);
  }
  const resolve = async (wire: string): Promise<MonitoringTarget> => {
    id(wire);
    const bound = byWire.get(wire);
    const matches = bound ? [await directory.get(bound, scope)].filter(b => b !== null)
      : await directory.exact(wire, scope);
    if (!matches.length) throw new MonitoringError('TARGET_UNRESOLVED', '上报 Bot 无法解析为有效目录目标。');
    if (matches.length !== 1) throw new MonitoringError('TARGET_AMBIGUOUS', '上报 Bot 对应多个目标，需要核实采集身份绑定。');
    const resolved = target(matches[0]);
    // A bound target cannot also accept its bare ID: aliases must remain one-to-one.
    if (byTarget.has(targetKey(resolved)) && byTarget.get(targetKey(resolved)) !== wire) {
      throw new MonitoringError('TARGET_BINDING_CONFLICT', '该目标使用已审核的其他上报编号。');
    }
    return resolved;
  };
  return {
    resolve,
    async legacyId(t) {
      const wire = byTarget.get(targetKey(t)) ?? t.botId;
      try { return targetKey(await resolve(wire)) === targetKey(t) ? wire : null; }
      catch (e) {
        if (e instanceof MonitoringError && ['TARGET_UNRESOLVED', 'TARGET_AMBIGUOUS', 'TARGET_BINDING_CONFLICT'].includes(e.code)) return null;
        throw e;
      }
    },
  };
}
