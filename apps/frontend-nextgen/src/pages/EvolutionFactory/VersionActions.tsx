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
  if (!c) return <span className="text-xs text-muted-foreground">未评测</span>;
  const delta = typeof c.delta === 'number' && Number.isFinite(c.delta) ? c.delta : null;
  return (
    <div className="flex flex-nowrap items-center gap-x-2 whitespace-nowrap text-[13px]" aria-label={c.name || 'test_score'}>
      <span className="whitespace-nowrap">
        {score(c.baseline)} → {score(c.candidate)}
      </span>
      <span
        className={
          delta === null || delta === 0 ? 'text-muted-foreground' : delta > 0 ? 'text-success' : 'text-destructive'
        }
      >
        {delta === null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(4)}`}
      </span>
    </div>
  );
}
const actionClass = 'h-7 shrink-0 rounded-md px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary';
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
      <div className="flex flex-nowrap items-center gap-1 whitespace-nowrap">
        {version.diff?.available && (
          <Button size="sm" variant="ghost" className={actionClass} onClick={() => onDiff(version)}>
            Diff
          </Button>
        )}
        {version.pack?.status === 'available' && (
          <>
            <Button size="sm" variant="ghost" className={actionClass} disabled={busy} onClick={() => void download()}>
              下载
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className={actionClass}
              aria-label="应用 Pack"
              onClick={() =>
                history.push(
                  `/evolution-factory/new?type=pack_restore&packId=${encodeURIComponent(version.pack!.packId)}`,
                )
              }
            >
              应用
            </Button>
          </>
        )}
      </div>
      <ErrorBox message={error} />
    </div>
  );
}
