import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { AvatarSettings } from '@/components/BotWorkshop/BotAvatar/AvatarSettings';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { BotDomain } from '@/domain/botWorkshop';
import { ArrowLeft, Save } from 'lucide-react';
export function DetailHeader({
  bot,
  editable,
  isOwner,
  onBack,
}: {
  bot: BotDomain;
  editable: boolean;
  isOwner: boolean;
  onBack: () => void;
}) {
  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card px-4 sm:px-6">
      <Button
        variant="ghost"
        size="icon"
        aria-label="返回 Bot 工坊"
        onClick={onBack}
        leftIcon={<ArrowLeft className="size-4" />}
      />
      {bot.deployment === 'local' ? (
        <AvatarSettings botId={bot.id} editable={editable && isOwner} />
      ) : (
        <BotAvatar name={bot.name} />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="m-0 truncate text-base font-semibold">{bot.name}</h1>
          <Badge className="shrink-0" tone={editable ? 'primary' : 'neutral'}>
            {editable ? '编辑模式' : '只读模式'}
          </Badge>
        </div>
        <p className="m-0 mt-0.5 text-xs text-muted-foreground">
          {bot.runtime.engine} · {bot.deployment === 'local' ? '本地' : '云端'}
        </p>
      </div>
      <Button leftIcon={<Save className="size-4" />} onClick={onBack}>
        保存并退出
      </Button>
    </header>
  );
}
