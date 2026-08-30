import { Button } from '@/components/ui';
import type { ImageUploadState } from '@/pages/Workspace/hooks/useGroupChatImageUpload';
import { cn } from '@/utils/cn';
import { X } from 'lucide-react';

export interface GroupImagePreviewProps {
  images: Array<{ id: string; previewUrl: string; name: string }>;
  uploadStates: Record<string, ImageUploadState>;
  maxCount: number;
  onRemove: (id: string) => void;
}

/** 群聊图片预览面板：缩略图 + 删除按钮 + 上传进度/失败覆盖（对齐 open-claw「我的协作」）。 */
export function GroupImagePreview({ images, uploadStates, maxCount, onRemove }: GroupImagePreviewProps) {
  if (images.length === 0) return null;

  return (
    <div className="flex gap-2 overflow-x-auto rounded-lg bg-[var(--color-panel-strong)] p-2">
      {images.map((image) => {
        const state = uploadStates[image.id];
        const isUploading = state?.status === 'uploading';
        const isError = state?.status === 'error';
        const progressPercent = state ? Math.round(state.progress * 100) : 0;

        return (
          <div key={image.id} className="flex shrink-0 flex-col items-center gap-1">
            <div className="relative h-11 w-11 overflow-visible rounded-md shadow-sm transition-shadow">
              <img
                src={image.previewUrl}
                alt={image.name}
                className={cn(
                  'h-11 w-11 rounded-md border border-[var(--color-border)] bg-[var(--color-panel-muted)] object-cover',
                  isError && 'opacity-50',
                )}
              />
              {isUploading && (
                <div className="absolute inset-0 flex items-center justify-center rounded-md bg-black/30 text-[10px] font-medium text-white">
                  {progressPercent}%
                </div>
              )}
              {isError && (
                <div className="absolute inset-0 flex items-center justify-center rounded-md text-[10px] text-[var(--color-error)]">
                  失败
                </div>
              )}
              <Button
                type="button"
                variant="ghost"
                aria-label="移除图片"
                onClick={() => onRemove(image.id)}
                className="absolute -right-1.5 -top-1.5 h-5 w-5 rounded-full border-2 border-white bg-[var(--color-muted)] p-0 text-white shadow-sm hover:scale-110 hover:bg-[var(--color-muted)]"
              >
                <X className="h-2.5 w-2.5" />
              </Button>
            </div>
            <span className="max-w-[44px] overflow-hidden text-ellipsis whitespace-nowrap text-[10px] font-medium text-[var(--color-muted)]">
              {image.name.length > 8 ? `${image.name.slice(0, 8)}…` : image.name}
            </span>
          </div>
        );
      })}
      {images.length >= maxCount && (
        <div className="self-center text-xs font-medium text-amber-500">已达到最大数量限制 ({maxCount})</div>
      )}
    </div>
  );
}

export default GroupImagePreview;
