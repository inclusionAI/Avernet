import { Button } from '@/components/ui/Button';
import { useCallback, useState } from 'react';
import { ErrorBox, StatusBadge, useResource } from './shared';
import { useEvolutionContext } from './useEvolutionContext';
export default function TaskLogArchives({ taskId }: { taskId: string }) {
  const identity = useEvolutionContext();
  const evolutionApi = identity.api;
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  const logs = useResource(
    useCallback(() => evolutionApi.logArchives(taskId), [identity.key, taskId]),
    identity.ready && open,
  );
  const act = async (archiveId?: string) => {
    setBusy(true);
    setError('');
    try {
      if (archiveId) {
        const value = await evolutionApi.logDownload(taskId, archiveId);
        if (!/^https?:\/\//.test(value.url)) throw new Error('下载地址不可用');
        window.location.assign(value.url);
      } else {
        await evolutionApi.requestLogArchive(taskId);
        logs.reload();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '日志操作失败');
    } finally {
      setBusy(false);
    }
  };
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)} className="mt-4 rounded-lg border border-border p-3">
      <summary className="cursor-pointer text-sm">任务运行日志归档</summary>
      <div className="mt-3 space-y-3">
        <ErrorBox message={error || logs.error} retry={logs.reload} />
        <div className="flex gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void act()}>
            获取最新日志
          </Button>
          <Button size="sm" variant="ghost" onClick={logs.reload}>
            刷新归档
          </Button>
        </div>
        {!logs.loading && !logs.error && !logs.data?.items.length && (
          <p className="text-xs text-muted-foreground">暂无日志归档，可按需获取。</p>
        )}
        {logs.data?.items.map((item) => (
          <div key={String(item.archiveId)} className="flex flex-wrap items-center gap-3 text-xs">
            <span className="break-all">{String(item.archiveId)}</span>
            <StatusBadge status={String(item.status)} />
            {item.status === 'succeeded' && (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act(String(item.archiveId))}>
                下载日志
              </Button>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
