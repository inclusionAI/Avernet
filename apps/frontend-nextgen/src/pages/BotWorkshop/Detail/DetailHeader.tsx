import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { AvatarSettings } from '@/components/BotWorkshop/BotAvatar/AvatarSettings';
import { Button } from '@/components/ui/Button';
import type { BotDomain } from '@/domain/botWorkshop';
import { ArrowLeft, ChevronRight, Pencil, Save, SlidersHorizontal } from 'lucide-react';
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
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border bg-card px-4 sm:px-6">
      <Button
        variant="ghost"
        size="icon"
        className="size-8 rounded-md"
        aria-label="返回 Bot 管理"
        onClick={onBack}
        leftIcon={<ArrowLeft className="size-4" />}
      />
      <div className="hidden items-center gap-1 text-sm text-muted-foreground sm:flex">
        <span>Bot 管理</span>
        <ChevronRight className="size-3.5" aria-hidden />
      </div>
      <div className="min-w-0 flex flex-1 items-center gap-2">
        {bot.deployment === 'local' ? (
          <AvatarSettings botId={bot.id} editable={editable && isOwner} />
        ) : (
          <BotAvatar name={bot.name} className="size-7 rounded-full" />
        )}
        <h1 className="m-0 truncate text-base font-semibold text-foreground">{bot.name}</h1>
        {editable ? (
          <span className="hidden size-7 items-center justify-center rounded-md border border-border text-muted-foreground sm:inline-flex">
            <Pencil className="size-3.5" aria-hidden />
          </span>
        ) : null}
      </div>
      <span className="hidden h-8 items-center gap-2 rounded-lg border border-border px-3 text-sm text-muted-foreground sm:inline-flex">
        <SlidersHorizontal className="size-4" aria-hidden />
        调试对话
      </span>
      <Button className="px-4" leftIcon={<Save className="size-4" />} onClick={onBack}>
        保存
      </Button>
    </header>
  );
}
