import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { SquareResource } from '@/domain/collaborationSquare/types';
import { getCollaborationSquareShareUrl } from '@/utils/collaborationSquare';
import { useCallback } from 'react';

export function useCollaborationSquareClipboardActions() {
  const copyText = useCallback(async (value: string, success: string) => {
    try {
      await navigator.clipboard.writeText(value);
      notifySuccess(success);
    } catch {
      notifyError('复制失败，请检查浏览器剪贴板权限');
    }
  }, []);
  const share = useCallback(
    (resource: SquareResource, id: string, searchHint?: string) => {
      const origin = typeof window === 'undefined' ? '' : window.location.origin;
      void copyText(getCollaborationSquareShareUrl(origin, resource, id, searchHint), '分享链接已复制');
    },
    [copyText],
  );
  const copyBotId = useCallback((id: string) => void copyText(id, 'Bot UUID 已复制'), [copyText]);

  return { copyBotId, share };
}
