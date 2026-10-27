import type { CommunityAttachment } from '@/domain/community/types';
import { MAX_COMMUNITY_ATTACHMENT_BYTES } from '@/services/community/communityService';

export type DraftErrors = {
  title?: string;
  body?: string;
  pollOptions?: string;
  pollDeadline?: string;
  shareAttachments?: string;
  helpExpectedTime?: string;
};

export type AttachmentDraft = {
  key: string;
  file: File;
  status: 'uploading' | 'uploaded' | 'error';
  attachment?: CommunityAttachment;
  error?: string;
};

export function attachmentValidationError(file: File): string | undefined {
  if (!file.size) return '不能上传空文件';
  if (file.size > MAX_COMMUNITY_ATTACHMENT_BYTES) return '单个附件不能超过 20 MiB';
  return undefined;
}
