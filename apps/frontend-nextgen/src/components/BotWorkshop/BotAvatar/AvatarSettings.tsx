import { Button } from '@/components/ui/Button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Spin } from '@/components/ui/Spin';
import { appExtension } from '@/extensions';
import { useBotAvatar } from '@/hooks/useBotAvatar';
import { Suspense, useCallback, useState } from 'react';
import type { AvatarProcessingStatus } from './AvatarEditor';

export function AvatarSettings({
  botId,
  editable,
  trigger,
  onStagedChange,
  onOverlayOpenChange,
  onProcessingChange,
}: {
  botId: string;
  editable: boolean;
  /** 可选的触发器节点，缺省为原轮廓小按钮；由调用方提供时承担头像编辑入口。 */
  trigger?: React.ReactNode;
  /** 编辑器内仅本地暂存头像，不落库；由宿主在最终保存时统一持久化。 */
  onStagedChange?: (url: string | undefined) => void;
  /** 编辑器宿主浮层（上传/AIGC）开合上报；宿主用于抑制外层 Dialog 的外点关闭连坐。 */
  onOverlayOpenChange?: (open: boolean) => void;
  onProcessingChange?: (status: AvatarProcessingStatus) => void;
}) {
  const avatar = useBotAvatar(botId);
  const Editor = appExtension.avatarEditor;
  // 编辑器浮层开时其点击都在 popover 外，须抑制 Radix 外点关闭，否则浮层被连坐卸载。
  const [overlayOpen, setOverlayOpen] = useState(false);
  const reportOverlayOpen = useCallback(
    (open: boolean) => {
      setOverlayOpen(open);
      onOverlayOpenChange?.(open);
    },
    [onOverlayOpenChange],
  );
  return (
    <Popover onOpenChange={(open) => { if (!open) onProcessingChange?.('idle'); }}>
      <PopoverTrigger asChild>
        {trigger ?? (
          <Button variant="outline" size="sm">
            {avatar.value ? <img src={avatar.value} alt="Bot 头像" className="size-6" /> : '头像'}
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent
        className="w-80 space-y-3"
        onInteractOutside={(event) => {
          if (overlayOpen) event.preventDefault();
        }}
        onFocusOutside={(event) => {
          if (overlayOpen) event.preventDefault();
        }}
        onEscapeKeyDown={(event) => {
          if (overlayOpen) event.preventDefault();
        }}
      >
        {avatar.error ? (
          <p role="alert" className="text-xs text-destructive">
            {avatar.error}
          </p>
        ) : null}
        <Suspense fallback={<Spin tip="加载头像编辑器…" />}>
          <Editor
            value={avatar.value}
            onChange={(url) => {
              avatar.setValue(url);
              onStagedChange?.(url);
            }}
            seed={botId}
            disabled={!editable || avatar.loading}
            onOverlayOpenChange={reportOverlayOpen}
            onProcessingChange={onProcessingChange}
          />
        </Suspense>
        <p className="m-0 text-[11px] text-muted-foreground">修改仅本地暂存，点击弹窗的「保存修改」后生效</p>
      </PopoverContent>
    </Popover>
  );
}
