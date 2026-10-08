import type { SessionMessageAttachment } from '@/services/backendApi/collaboration/sessionController';
import { sessionFileService } from './sessionFileService';
import { extractPartUrl, resolveUploadMime, SESSION_FILE_MULTIPART_THRESHOLD } from './sessionFileUtils';

export type { SessionMessageAttachment };

const IMAGE_SHARE_TTL_SECONDS = 3600;

export interface UploadAttachmentOptions {
  signal?: AbortSignal;
  onProgress?: (loaded: number, total: number) => void;
}

function friendlyError(error: { friendlyMessage: string }): Error {
  return new Error(error.friendlyMessage);
}

/**
 * 把一张图片上传到当前会话文件域，并组装成 BCS chat.send 的 image 附件。
 *
 * 采用与 open-claw「我的协作」一致的 4 步编排：
 * prepare → PUT/content → complete → share，最终 attachment.url 为免鉴权分享链接。
 */
export async function uploadChatImageAttachment(
  sessionId: string,
  file: File,
  options: UploadAttachmentOptions = {},
): Promise<SessionMessageAttachment> {
  const mime = resolveUploadMime(file.name, file.type);
  const prepared = await sessionFileService.prepareUpload(sessionId, {
    file_name: file.name,
    size: file.size,
    mime_type: mime,
  });
  if (!prepared.ok) throw friendlyError(prepared.error);

  const { fileId, uploadUrl, parts } = prepared.data;
  const errorOptions = { signal: options.signal, onProgress: options.onProgress };

  if (file.size >= SESSION_FILE_MULTIPART_THRESHOLD && parts && parts.length > 1) {
    const partCount = parts.length;
    const chunkSize = Math.ceil(file.size / partCount);
    for (let index = 0; index < partCount; index += 1) {
      const start = index * chunkSize;
      const end = Math.min(start + chunkSize, file.size);
      const chunk = file.slice(start, end);
      const partUrl = extractPartUrl(parts[index]);
      if (partUrl) {
        await sessionFileService.uploadBytes(partUrl, chunk, { mime, ...errorOptions });
      } else {
        await sessionFileService.uploadContent(sessionId, fileId, chunk, {
          mime,
          part: index,
          ...errorOptions,
        });
      }
    }
  } else if (uploadUrl) {
    await sessionFileService.uploadBytes(uploadUrl, file, { mime, ...errorOptions });
  } else {
    await sessionFileService.uploadContent(sessionId, fileId, file, { mime, ...errorOptions });
  }

  const completed = await sessionFileService.completeUpload(sessionId, fileId);
  if (!completed.ok) throw friendlyError(completed.error);

  const shared = await sessionFileService.shareFileWithExpiry(sessionId, fileId, IMAGE_SHARE_TTL_SECONDS);
  if (!shared.ok) throw friendlyError(shared.error);

  return {
    attachment_id: completed.data.fileId,
    type: 'image',
    file_name: completed.data.name,
    mime_type: completed.data.mimeType,
    size: completed.data.size,
    url: shared.data.shareUrl,
    expires_at: shared.data.expiresAt,
  };
}

/** 批量上传图片附件；单张失败不阻断其它图片，也不抛出异常。 */
export async function uploadChatImageAttachments(
  sessionId: string,
  files: File[],
  options: UploadAttachmentOptions = {},
): Promise<SessionMessageAttachment[]> {
  if (files.length === 0) return [];
  const settled = await Promise.allSettled(files.map((file) => uploadChatImageAttachment(sessionId, file, options)));
  return settled
    .filter((item): item is PromiseFulfilledResult<SessionMessageAttachment> => item.status === 'fulfilled')
    .map((item) => item.value);
}
