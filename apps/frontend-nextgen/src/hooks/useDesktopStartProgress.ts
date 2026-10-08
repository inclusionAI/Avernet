import {
  deriveDesktopStartProgress,
  type DesktopStartProgressState,
} from '@/services/botWorkshop/desktopStartProgress';
import { localBotService } from '@/services/botWorkshop/localBotService';
import { useEffect, useState } from 'react';
export function useDesktopStartProgress(botId: string) {
  const [state, setState] = useState<DesktopStartProgressState>({ stepIndex: 0, outcome: 'running' });
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let step = 0;
    setState({ stepIndex: 0, outcome: 'running' });
    setError('');
    const poll = async () => {
      let terminal = false;
      try {
        const data = await localBotService.progress(botId);
        if (!active) return;
        const next = deriveDesktopStartProgress(data, step);
        step = Math.max(step, next.stepIndex);
        setState((previous) => ({
          ...previous,
          ...next,
          stepIndex: step,
          percent: next.percent ?? previous.percent,
          detail: next.detail ?? previous.detail,
        }));
        setError('');
        terminal = next.outcome !== 'running';
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : '启动进度查询失败');
      }
      if (active && !terminal) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [botId, revision]);
  return { state, error, retry: () => setRevision((n) => n + 1) };
}
