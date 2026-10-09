import { Button } from '@/components/ui/Button';
import { useDesktopStartProgress } from '@/hooks/useDesktopStartProgress';
import { DESKTOP_START_STEP_LABELS } from '@/services/botWorkshop/desktopStartProgress';
import { Check, Circle, LoaderCircle, X } from 'lucide-react';

export function DesktopStartProgress({ botId }: { botId: string }) {
  const { state, error, retry } = useDesktopStartProgress(botId);
  const failed = Boolean(error) || state.outcome === 'failed';
  return (
    <div id={`desktop-progress-${botId}`} className="space-y-4 px-6 py-4 text-xs" aria-live="polite">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="m-0 text-sm font-medium">桌面环境初始化</p>
          <p className="m-0 mt-1 text-muted-foreground">
            {state.outcome === 'success' ? '环境已就绪，等待 Bot 状态更新' : DESKTOP_START_STEP_LABELS[state.stepIndex]}
          </p>
        </div>
        {failed ? (
          <Button variant="outline" size="sm" onClick={retry}>
            重新查询
          </Button>
        ) : null}
      </div>
      <ol className="grid grid-cols-1 gap-2 sm:grid-cols-4" aria-label="桌面 Bot 初始化步骤">
        {DESKTOP_START_STEP_LABELS.map((label, index) => {
          const complete = index < state.stepIndex || state.outcome === 'success';
          const current = index === state.stepIndex && state.outcome !== 'success';
          const stepFailed = current && failed;
          return (
            <li
              key={label}
              className={`flex items-center gap-2 rounded-md border px-3 py-2 ${
                stepFailed ? 'border-destructive/40 bg-destructive/5 text-destructive' : 'border-border bg-card'
              }`}
            >
              {complete ? (
                <Check className="size-4 shrink-0 text-primary" />
              ) : stepFailed ? (
                <X className="size-4 shrink-0" />
              ) : current ? (
                <LoaderCircle className="size-4 shrink-0 animate-spin text-primary" />
              ) : (
                <Circle className="size-4 shrink-0 text-muted-foreground" />
              )}
              <span>{label}</span>
            </li>
          );
        })}
      </ol>
      {state.percent !== undefined ? (
        <div className="space-y-1">
          <div className="flex justify-between text-muted-foreground">
            <span>运行环境下载</span>
            <span>{state.percent}%</span>
          </div>
          <progress className="h-2 w-full" max={100} value={state.percent} aria-label="下载进度" />
        </div>
      ) : null}
      {state.detail ? <p className="m-0 text-muted-foreground">{state.detail}</p> : null}
      {failed ? (
        <p role="alert" className="m-0 text-destructive">
          {error || state.message || '启动失败'}
        </p>
      ) : state.message ? (
        <p className="m-0 text-muted-foreground">{state.message}</p>
      ) : null}
    </div>
  );
}
