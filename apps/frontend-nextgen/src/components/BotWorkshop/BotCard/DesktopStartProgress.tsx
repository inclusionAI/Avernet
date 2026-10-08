import { Button } from '@/components/ui/Button';
import { useDesktopStartProgress } from '@/hooks/useDesktopStartProgress';
import { DESKTOP_START_STEP_LABELS } from '@/services/botWorkshop/desktopStartProgress';
export function DesktopStartProgress({ botId }: { botId: string }) {
  const { state, error, retry } = useDesktopStartProgress(botId);
  return (
    <div className="max-w-56 space-y-1 text-xs" aria-live="polite">
      <span>
        {state.outcome === 'success' ? '环境已就绪，等待 Bot 状态更新' : DESKTOP_START_STEP_LABELS[state.stepIndex]}
      </span>
      {state.percent !== undefined ? (
        <progress className="w-full" max={100} value={state.percent} aria-label="下载进度" />
      ) : null}
      {state.detail ? <p className="text-muted-foreground">{state.detail}</p> : null}
      {state.message ? <p>{state.message}</p> : null}
      {error || state.outcome === 'failed' ? (
        <>
          <p role="alert" className="text-destructive">
            {error || state.message || '启动失败'}
          </p>
          <Button variant="outline" size="sm" onClick={retry}>
            重新查询
          </Button>
        </>
      ) : null}
    </div>
  );
}
