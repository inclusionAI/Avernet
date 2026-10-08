import { Button } from '@/components/ui/Button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Spin } from '@/components/ui/Spin';
import { appExtension } from '@/extensions';
import { useBotAvatar } from '@/hooks/useBotAvatar';
import { Suspense } from 'react';
export function AvatarSettings({ botId, editable }: { botId: string; editable: boolean }) {
  const avatar = useBotAvatar(botId);
  const Editor = appExtension.avatarEditor;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm">
          {avatar.value ? <img src={avatar.value} alt="Bot 头像" className="size-6" /> : '头像'}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-80 space-y-3">
        {avatar.error ? (
          <p role="alert" className="text-xs text-destructive">
            {avatar.error}
          </p>
        ) : null}
        <Suspense fallback={<Spin tip="加载头像编辑器…" />}>
          <Editor
            value={avatar.value}
            onChange={avatar.setValue}
            seed={botId}
            disabled={!editable || avatar.loading || avatar.saving}
          />
        </Suspense>
        <Button disabled={!editable || avatar.loading || avatar.saving} onClick={() => void avatar.save()}>
          保存头像
        </Button>
      </PopoverContent>
    </Popover>
  );
}
