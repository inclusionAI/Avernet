import { Button } from '@/components/ui/Button';
import type { EvolutionVersion } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import { useState } from 'react';
import { ErrorBox } from './shared';
import { useEvolutionContext } from './useEvolutionContext';

export const versionLabel = (v: EvolutionVersion) =>
  v.kind === 'initial' ? 'Initial' : v.kind === 'snapshot' ? '主动快照' : `R${v.round ?? 0}`;
export function versionStatus(v: EvolutionVersion) {
  const labels: Record<string, string> = {
    accepted: '已接受',
    accepted_unregistered: '已接受 · Pack 未登记',
    promotion_failed: '晋升失败',
    passed_not_promoted: '通过 Bench · 未晋升',
    unregistered: 'Pack 未登记',
    rejected: '未接受',
  };
  return (
    labels[v.acceptanceStatus] ||
    (v.kind === 'initial' ? '初始版本' : v.kind === 'snapshot' ? '未评测快照' : '状态未知')
  );
}
export function stableVersion(items: EvolutionVersion[]) {
  return (
    items
      .filter((v) => v.kind === 'round' && v.accepted === true && v.pack?.status === 'available')
      .sort((a, b) => (b.round ?? 0) - (a.round ?? 0))[0] ||
    items.find((v) => v.kind === 'initial' && v.pack?.status === 'available')
  );
}
const score = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? Number(v.toFixed(4)).toString() : '—');
export function Comparison({ version }: { version: EvolutionVersion }) {
  const c = version.scoreComparison;
  return c ? (
    <div className="text-xs">
      <p className="whitespace-nowrap">
        {score(c.baseline)} → <strong>{score(c.candidate)}</strong> · Δ {score(c.delta)}
      </p>
      <p className="mt-1 text-muted-foreground">{c.name || 'test_score'}</p>
    </div>
  ) : (
    <span className="text-xs text-muted-foreground">未评测</span>
  );
}
export function VersionActions({
  version,
  onDiff,
}: {
  version: EvolutionVersion;
  onDiff: (v: EvolutionVersion) => void;
}) {
  const { api: evolutionApi } = useEvolutionContext();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const download = async () => {
    setBusy(true);
    setError('');
    try {
      const value = await evolutionApi.packDownload(
        version.taskId,
        version.stepId,
        version.kind === 'initial' ? 'baseline' : version.kind,
        version.round,
      );
      if (typeof value.url !== 'string' || !/^https?:\/\//.test(value.url)) throw new Error('下载地址不可用');
      window.location.assign(value.url);
    } catch (e) {
      setError(e instanceof Error ? e.message : '下载失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-2">
        {version.diff?.available && (
          <Button size="sm" variant="ghost" onClick={() => onDiff(version)}>
            Diff
          </Button>
        )}
        {version.pack?.status === 'available' && (
          <>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => void download()}>
              下载
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() =>
                history.push(
                  `/evolution-factory/new?type=pack_restore&packId=${encodeURIComponent(version.pack!.packId)}`,
                )
              }
            >
              应用 Pack
            </Button>
          </>
        )}
      </div>
      <ErrorBox message={error} />
    </div>
  );
}
