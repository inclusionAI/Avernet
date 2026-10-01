import type { SessionMessageAttachment } from '@/services/workspace/groupChatAttachmentService';
import { uploadChatImageAttachment } from '@/services/workspace/groupChatAttachmentService';
import type { PendingImage } from '@tc-chat/ui/es/Sender/hooks/useImageUpload';
import { useImageUpload } from '@tc-chat/ui/es/Sender/hooks/useImageUpload';
import { useCallback, useRef, useState } from 'react';
import { toast } from 'sonner';

const MAX_IMAGE_COUNT = 9;
const MAX_IMAGE_SIZE_MB = 10;
const MAX_TOTAL_IMAGE_SIZE_MB = 90;

/** 单张图片的上传状态（覆盖在缩略图上方做进度提示）。 */
export interface ImageUploadState {
  status: 'pending' | 'uploading' | 'ready' | 'error';
  progress: number;
}

/**
 * human 群聊输入框图片暂存与发送：
 * SDK useImageUpload 负责校验/压缩/预览/粘贴，发送时再上传到会话文件域并组装 BCS 附件。
 * 上传时逐图追踪进度，UI 覆盖在缩略图上显示百分比（对齐 open-claw「我的协作」）。
 */
export function useGroupChatImageUpload(sessionId: string | null | undefined) {
  const images = useImageUpload({
    maxCount: MAX_IMAGE_COUNT,
    maxSize: MAX_IMAGE_SIZE_MB,
    maxTotalSize: MAX_TOTAL_IMAGE_SIZE_MB,
  });
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStates, setUploadStates] = useState<Record<string, ImageUploadState>>({});
  const statesRef = useRef<Record<string, ImageUploadState>>({});

  const commitStates = useCallback((next: Record<string, ImageUploadState>) => {
    statesRef.current = next;
    setUploadStates(next);
  }, []);

  const patchState = useCallback(
    (id: string, patch: Partial<ImageUploadState>) => {
      commitStates({ ...statesRef.current, [id]: { ...statesRef.current[id], ...patch } });
    },
    [commitStates],
  );

  const safeAddImages = useCallback(
    (files: File[]) => {
      void images.addImages(files).catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : '图片添加失败');
      });
    },
    [images],
  );

  const uploadAll = useCallback(async (): Promise<SessionMessageAttachment[]> => {
    if (!sessionId || images.images.length === 0) return [];
    setIsUploading(true);
    // 初始化所有图片为 pending
    const initial: Record<string, ImageUploadState> = {};
    for (const img of images.images) {
      initial[img.id] = { status: 'pending', progress: 0 };
    }
    commitStates(initial);

    try {
      const results = await Promise.allSettled(
        images.images.map(async (image: PendingImage): Promise<SessionMessageAttachment> => {
          patchState(image.id, { status: 'uploading', progress: 0 });
          const attachment = await uploadChatImageAttachment(sessionId, image.file, {
            onProgress: (loaded, total) => {
              patchState(image.id, { progress: total ? loaded / total : 0 });
            },
          });
          patchState(image.id, { status: 'ready', progress: 1 });
          return attachment;
        }),
      );
      const attachments = results
        .filter((r): r is PromiseFulfilledResult<SessionMessageAttachment> => r.status === 'fulfilled')
        .map((r) => r.value);
      // 标记失败的图片
      results.forEach((r, index) => {
        if (r.status === 'rejected') {
          patchState(images.images[index].id, { status: 'error' });
        }
      });
      if (attachments.length < images.images.length) {
        toast.error('部分图片上传失败，已跳过');
      }
      return attachments;
    } finally {
      setIsUploading(false);
    }
  }, [images.images, sessionId, commitStates, patchState]);

  const clear = useCallback(() => {
    commitStates({});
    images.clearImages();
  }, [commitStates, images]);

  const removeImage = useCallback(
    (id: string) => {
      const next = { ...statesRef.current };
      delete next[id];
      commitStates(next);
      images.removeImage(id);
    },
    [commitStates, images],
  );

  return {
    images: images.images,
    isProcessing: images.isProcessing,
    isUploading,
    canAddMore: images.canAddMore,
    addFiles: safeAddImages,
    removeImage,
    clear,
    uploadAll,
    uploadStates,
  };
}
