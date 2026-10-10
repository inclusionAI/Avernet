import { notifyError, notifySuccess } from '@/components/ui/notify';
import { useCallback } from 'react';

export function useResourcePathClipboard() {
  return useCallback(async (path: string) => {
    try {
      await navigator.clipboard.writeText(path);
      notifySuccess('资源路径已复制');
    } catch {
      notifyError(`复制失败，请手动复制：${path}`);
    }
  }, []);
}
