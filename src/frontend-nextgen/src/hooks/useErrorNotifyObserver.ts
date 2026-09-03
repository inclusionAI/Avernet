import { notifyError } from '@/components/ui/notify';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useEffect, useRef } from 'react';

/**
 * 顶层观察者(global-error-notify-dedup D1/D7):订阅 `errorNotifyStore`,把协议层(通道 B `backendRequest`、
 * 通道 A `requestConfig`)入队的失败记录,在 Hook 层统一经 `notifyError` 发起提示。
 *
 * 为何副作用在 Hook 而非协议层:`src/services` 禁 toast/DOM;协议层只 enqueue+throw,toast 上移到本观察者。
 *
 * 时序(opt-out 的命门):用 `setTimeout(flush, 0)` 延迟兜底发起——Hook 的 catch 走微任务、先于 0ms 宏任务执行,
 * 因此 Hook 在 catch 中调 `errorNotifyStore.cancel(toastKey)` 可在兜底发起前取消(自定义接管或静默)。
 *
 * 去重:实际去重由 `notifyError(message,{ id: toastKey })` 的冷静窗 + sonner 同 id 合并完成;本观察者每条非取消
 * 记录调一次 notifyError,重复入队由 notifyError 收敛。`drain` 一次性取空并清队列,避免重复发起。
 *
 * 与 `useGatewayLoginRedirect` 同构;挂载见 `src/app.tsx` 的 `ErrorNotifyObserver`(rootContainer 全局唯一)。
 */
export function useErrorNotifyObserver(): void {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const flush = (): void => {
      timerRef.current = null;
      const items = useErrorNotifyStore.getState().drain();
      for (const it of items) {
        if (it.cancelled) continue; // Hook 已 cancel(自定义接管或静默),跳过默认提示。
        notifyError(it.message, { id: it.toastKey });
      }
    };
    const schedule = (): void => {
      if (timerRef.current !== null) return; // 合并同 tick 内多次入队为一次 flush。
      timerRef.current = setTimeout(flush, 0);
    };
    const unsubscribe = useErrorNotifyStore.subscribe(schedule);
    schedule(); // 补刷观察者挂载前已入队的记录。
    return () => {
      unsubscribe();
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);
}
