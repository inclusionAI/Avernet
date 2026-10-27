import { notifyError } from '@/components/ui/notify';
import { communityErrorMessage } from '@/domain/community/errors';
import type { CommunityAttachment } from '@/domain/community/types';
import { communityService } from '@/services/community';
import { useCallback } from 'react';

export function useCommunityAttachments(currentUserId: string) {
  const uploadAttachment = useCallback(
    (file: File) => communityService.uploadAttachment(file, currentUserId || 'current-user'),
    [currentUserId],
  );

  const downloadAttachment = useCallback(async (attachment: CommunityAttachment) => {
    try {
      const blob = await communityService.downloadAttachment(attachment.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = attachment.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      notifyError(communityErrorMessage(error, '附件下载失败'), { title: '下载失败' });
    }
  }, []);

  return { uploadAttachment, downloadAttachment };
}
