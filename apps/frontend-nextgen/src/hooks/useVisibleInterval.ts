import { useEffect } from 'react';

export function useVisibleInterval(callback: () => void, intervalMs: number, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    // Tern/qiankun 容器中子应用的 visibilityState 可能长期是 hidden，
    // 定时同步不以它为前置条件，恢复可见时仍立即补一次。
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') callback();
    };
    const timer = window.setInterval(callback, intervalMs);
    document.addEventListener('visibilitychange', refreshWhenVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', refreshWhenVisible);
    };
  }, [callback, enabled, intervalMs]);
}
