import BotAvatar from '@/components/BotWorkshop/BotAvatar';
import { BotBasicInfoDialog } from '@/components/BotWorkshop/BotBasicInfoDialog';
import { Button } from '@/components/ui/Button';
import type { BotDomain } from '@/domain/botWorkshop';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import { cn } from '@/utils/cn';
import { ArrowLeft, ChevronRight, Pencil, Save, SlidersHorizontal } from 'lucide-react';
import { useState } from 'react';
export function DetailHeader({
  bot,
  editable,
  isOwner,
  debugOpen,
  saving,
  onBack,
  onOpenDebug,
  onSaveBasic,
  onReload,
}: {
  bot: BotDomain;
  editable: boolean;
  isOwner: boolean;
  debugOpen: boolean;
  saving: boolean;
  onBack: () => void;
  onOpenDebug: () => void;
  onSaveBasic: (values: { name: string; description: string }) => Promise<void>;
  onReload: () => void;
}) {
  const [basicInfoOpen, setBasicInfoOpen] = useState(false);
  const canEditBasic = editable && isOwner;
  const avatarUrl = bot.avatarUrl || generateBotAvatar(bot.id || bot.name);
  return (
    // 对话区可见(lg+)时补右描边,与下方面板区的 border-r 接成一条竖线。
    <header
      className={cn(
        'flex h-14 shrink-0 items-center gap-2 border-b border-border bg-card px-4 sm:px-6',
        debugOpen && 'lg:border-r lg:border-border',
      )}
    >
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
        <BotAvatar name={bot.name} avatarUrl={avatarUrl} className="size-7 rounded-full" />
        <h1 className="m-0 truncate text-base font-semibold text-foreground">{bot.name}</h1>
        {canEditBasic ? (
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            aria-label="编辑 Bot 基础信息"
            leftIcon={<Pencil className="size-3.5" />}
            onClick={() => setBasicInfoOpen(true)}
          />
        ) : null}
      </div>
      <Button
        variant="outline"
        size="sm"
        className="hidden gap-2 lg:inline-flex"
        aria-pressed={debugOpen}
        onClick={onOpenDebug}
        leftIcon={<SlidersHorizontal className="size-4" />}
      >
        调试对话
      </Button>
      <Button className="px-4" leftIcon={<Save className="size-4" />} onClick={onBack}>
        保存
      </Button>
      <BotBasicInfoDialog
        bot={bot}
        editable={canEditBasic}
        open={basicInfoOpen}
        saving={saving}
        onOpenChange={setBasicInfoOpen}
        onSave={onSaveBasic}
        onAvatarSaved={onReload}
      />
    </header>
  );
}
