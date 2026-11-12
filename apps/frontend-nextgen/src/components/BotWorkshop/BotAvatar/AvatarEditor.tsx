import { notifyError } from '@/components/ui';
import { Button } from '@/components/ui/Button';
import { generateBotAvatarBatch } from '@/services/botWorkshop/botAvatarGenerator';
import { ImageUp, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { prepareAvatar } from './prepareAvatar';

export type AvatarProcessingStatus = 'idle' | 'processing' | 'failed';
export interface BotAvatarEditorProps {
  value: string;
  onChange: (url: string) => void;
  seed: string;
  disabled?: boolean;
  /** 宿主自带上传入口时隐藏基础「上传图片」按钮，避免出现双上传入口。 */
  hideUpload?: boolean;
  /** 追加到动作行的额外按钮（如内部形态的 AI 生成头像），与「换一批/上传图片」同行同规格展示。 */
  extraActions?: React.ReactNode;
  /** 编辑器宿主浮层（上传/AIGC 等）开合上报；宿主据此抑制外层 Popover/Dialog 的外点关闭连坐。 */
  onOverlayOpenChange?: (open: boolean) => void;
  onProcessingChange?: (status: AvatarProcessingStatus) => void;
}
export default function AvatarEditor({ value, onChange, seed, disabled, hideUpload, extraActions, onProcessingChange }: BotAvatarEditorProps) {
  const [batch, setBatch] = useState(0);
  const uploadRef = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const variants = useMemo(() => generateBotAvatarBatch(seed, batch), [seed, batch]);
  useEffect(() => () => { generation.current += 1; }, []);

  const handleUpload = async (file?: File) => {
    if (!file) return;
    const current = ++generation.current;
    onProcessingChange?.('processing');
    try {
      const result = await prepareAvatar(file);
      if (generation.current !== current) return;
      onChange(result);
      onProcessingChange?.('idle');
    } catch (error) {
      if (generation.current !== current) return;
      onProcessingChange?.('failed');
      notifyError(error instanceof Error ? error.message : '头像处理失败，请重新选择');
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <img
          src={value || variants[0]}
          alt="当前 Bot 头像"
          className="size-14 rounded-xl border border-border object-cover"
        />
        <p className="m-0 text-[10px] leading-4 text-muted-foreground">
          支持从下方候选里挑选，或上传/生成新头像；
          <br />
          支持 PNG、JPEG、GIF、WebP，最大 2 MB；大图会缩小，动态图可能变为静态
        </p>
      </div>
      <div className="grid grid-cols-4 gap-2">
        {variants.map((url, i) => (
          <Button
            key={url}
            variant={value === url ? 'default' : 'outline'}
            type="button"
            disabled={disabled}
            aria-label={`选择头像 ${i + 1}`}
            className="h-auto p-1"
            onClick={() => { generation.current += 1; onChange(url); onProcessingChange?.('idle'); }}
          >
            <img src={url} alt="" className="size-12" />
          </Button>
        ))}
      </div>
      {/* 动作行：换一批 / 上传图片 / 宿主追加入口 同行等宽展示；nowrap + 紧凑内边距保证单行。 */}
      <div className="flex gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          className="flex-1 whitespace-nowrap px-2"
          leftIcon={<RefreshCw className="size-3.5" aria-hidden />}
          onClick={() => setBatch((n) => n + 1)}
        >
          换一批
        </Button>
        {hideUpload ? null : (
          <>
            <input
              ref={uploadRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              aria-label="上传 Bot 头像"
              className="sr-only"
              disabled={disabled}
              onChange={(event) => {
                void handleUpload(event.target.files?.[0]);
                event.target.value = '';
              }}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={disabled}
              className="flex-1 whitespace-nowrap px-2"
              leftIcon={<ImageUp className="size-3.5" aria-hidden />}
              onClick={() => uploadRef.current?.click()}
            >
              上传图片
            </Button>
          </>
        )}
        {extraActions}
      </div>
    </div>
  );
}
