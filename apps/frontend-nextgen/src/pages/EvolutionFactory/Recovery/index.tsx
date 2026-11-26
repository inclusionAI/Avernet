import { history, useLocation } from '@umijs/max';
import { ErrorBox } from '../shared';
import { RecoveryDetail } from './RecoveryDetail';
import { RecoveryList } from './RecoveryList';
import './recovery.css';

export default function RecoveryWorkspace({
  spaceId,
  ready,
  loading,
  error,
}: {
  spaceId?: string | number | null;
  loading: boolean;
  ready: boolean;
  error?: string | null;
}) {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const taskId = params.get('recoveryTaskId');
  function setParams(values: Record<string, string | undefined>) {
    const next = new URLSearchParams(location.search);
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined) next.delete(key);
      else next.set(key, value);
    }
    history.push(`${location.pathname}?${next}`);
  }
  return (
    <section id="evolution-recovery-panel" aria-labelledby="evolution-recovery-tab">
      {loading ? (
        <p role="status" className="text-sm text-muted-foreground">
          正在加载空间…
        </p>
      ) : !ready || error || !spaceId ? (
        <ErrorBox message={error || '请先选择可访问的空间'} />
      ) : taskId ? (
        <RecoveryDetail key={taskId} taskId={taskId} onBack={() => setParams({ recoveryTaskId: undefined })} />
      ) : (
        <RecoveryList
          spaceId={String(spaceId)}
          params={params}
          setParams={setParams}
          onOpen={(id) => setParams({ recoveryTaskId: id })}
        />
      )}
    </section>
  );
}
