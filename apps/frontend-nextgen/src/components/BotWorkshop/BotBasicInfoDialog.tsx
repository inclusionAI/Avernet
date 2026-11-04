import { getBotEngineLabel } from '@/domain/botEngine';
import type { BotDomain } from '@/domain/botWorkshop';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import {
  Modal,
  ModalContent,
  ModalDescription,
  ModalFooter,
  ModalHeader,
  ModalTitle,
} from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import { botAvatarService } from '@/services/botWorkshop/botAvatarService';
import { cn } from '@/utils/cn';
import { SquarePen } from 'lucide-react';
import { toast } from 'sonner';
import { useEffect, useState } from 'react';
import BotAvatar from './BotAvatar';
import { AvatarSettings } from './BotAvatar/AvatarSettings';

/** 输入余量不足 10% 时计数器转警示色，提示接近上限。 */
function FieldCounter({ value, max }: { value: number; max: number }) {
  return (
    <span
      aria-hidden
      className={cn(
        'text-[11px] tabular-nums',
        max - value <= Math.ceil(max / 10) ? 'text-destructive' : 'text-muted-foreground',
      )}
    >
      {value}/{max}
    </span>
  );
}

export function BotBasicInfoDialog({
  bot,
  editable,
  open,
  saving,
  onOpenChange,
  onSave,
  onAvatarSaved,
}: {
  bot: BotDomain;
  editable: boolean;
  open: boolean;
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (values: { name: string; description: string }) => Promise<void>;
  onAvatarSaved: () => void;
}) {
  const [name, setName] = useState(bot.name);
  const [description, setDescription] = useState(bot.description ?? '');
  // 头像在编辑器内仅本地暂存（undefined = 未改动），与名称/描述一起等「保存修改」统一落库。
  const [stagedAvatar, setStagedAvatar] = useState<string | undefined>(undefined);
  const [submitting, setSubmitting] = useState(false);
  // 头像编辑器宿主浮层（上传/AIGC）开时抑制本弹窗的外点关闭，避免浮层被连坐卸载。
  const [avatarOverlayOpen, setAvatarOverlayOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(bot.name);
    setDescription(bot.description ?? '');
    setStagedAvatar(undefined);
    setAvatarOverlayOpen(false);
  }, [bot.description, bot.name, open]);

  const avatarDirty = !!stagedAvatar && stagedAvatar !== (bot.avatarUrl ?? '');
  const dirty = avatarDirty || name.trim() !== bot.name || description.trim() !== (bot.description ?? '');
  const canSubmit = editable && !saving && !submitting && !!name.trim() && dirty;
  const engineLabel = getBotEngineLabel(bot.runtime.engine);
  const avatarUrl = stagedAvatar ?? (bot.avatarUrl || generateBotAvatar(bot.id || bot.name));
  // 名片实时预览：清空输入时回退到旧名称，避免预览区闪空。
  const previewName = name.trim() || bot.name;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      await onSave({ name: name.trim(), description: description.trim() });
      if (avatarDirty && stagedAvatar) {
        try {
          await botAvatarService.save(bot.id, stagedAvatar);
        } catch (e) {
          // 基础信息已保存，但头像失败时保留弹窗，避免用户丢上下文。
          toast.error(e instanceof Error ? e.message : '头像保存失败');
          return;
        }
        onAvatarSaved();
      }
      onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  // 名称输入框 Enter 直接保存；描述框 Enter 换行、⌘/Ctrl+Enter 保存。中文输入法组词确认的 Enter 不触发。
  const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    const inTextarea = (event.target as HTMLElement).tagName === 'TEXTAREA';
    if (inTextarea && !event.metaKey && !event.ctrlKey) return;
    event.preventDefault();
    void submit();
  };

  // 头像 tile：名片中的大头像本身即编辑入口，悬停出「更换头像」覆盖层。
  const avatarTileContent = (
    <>
      <BotAvatar name={bot.name} avatarUrl={avatarUrl} className="size-14 rounded-2xl" />
      <span className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-1 bg-black/55 opacity-0 transition-opacity group-hover:opacity-100">
        <SquarePen className="size-3.5 text-white" aria-hidden />
        <span className="text-[10px] leading-none text-white">更换头像</span>
      </span>
    </>
  );

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent
        size="md"
        // 头像浮层(上传/AIGC)打开期间释放焦点圈与外点关闭,避免浮层被连坐卸载、输入被抢焦。
        trapFocus={!avatarOverlayOpen}
        onInteractOutside={(event) => {
          if (avatarOverlayOpen) event.preventDefault();
        }}
        onFocusOutside={(event) => {
          if (avatarOverlayOpen) event.preventDefault();
        }}
        onEscapeKeyDown={(event) => {
          if (avatarOverlayOpen) event.preventDefault();
        }}
      >
        <ModalHeader>
          <ModalTitle>编辑 Bot 信息</ModalTitle>
          <ModalDescription className="sr-only">更新头像、名称与描述，将同步展示给你的协作伙伴</ModalDescription>
        </ModalHeader>
        <div className="space-y-5" onKeyDown={handleKeyDown}>
          {/* 名片实时预览卡：名称随输入同步，头像即编辑入口。 */}
          <div className="flex items-center gap-3.5 rounded-xl bg-muted/50 p-4">
            {open && editable ? (
              <AvatarSettings
                botId={bot.id}
                editable
                onStagedChange={setStagedAvatar}
                onOverlayOpenChange={setAvatarOverlayOpen}
                trigger={
                  <Button
                    variant="secondary"
                    size="icon"
                    aria-label="更换 Bot 头像"
                    className="group relative size-14 shrink-0 gap-0 overflow-hidden rounded-2xl"
                  >
                    {avatarTileContent}
                  </Button>
                }
              />
            ) : (
              <div className="relative shrink-0">{avatarTileContent}</div>
            )}
            <div className="min-w-0 flex-1">
              <p className="m-0 truncate text-[15px] font-semibold leading-6 text-foreground">{previewName}</p>
              <div className="mt-1 flex items-center gap-1.5">
                {engineLabel ? (
                  <span className="rounded-[4px] border border-divider px-1 text-[10px] font-medium leading-[15px] text-muted-foreground">
                    {engineLabel}
                  </span>
                ) : null}
                <span className="text-[11px] text-muted-foreground">名片实时预览</span>
              </div>
            </div>
          </div>
          {!editable ? (
            <p className="m-0 text-xs text-muted-foreground">当前身份没有该 Bot 的编辑权限</p>
          ) : null}
          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between">
              <label htmlFor="bot-basic-name" className="text-sm font-medium text-foreground">
                名称
              </label>
              <FieldCounter value={name.length} max={100} />
            </div>
            <Input
              id="bot-basic-name"
              value={name}
              disabled={!editable || saving}
              maxLength={100}
              autoFocus={open}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <div className="flex items-baseline justify-between">
              <label htmlFor="bot-basic-description" className="text-sm font-medium text-foreground">
                描述
              </label>
              <FieldCounter value={description.length} max={500} />
            </div>
            <Textarea
              id="bot-basic-description"
              value={description}
              disabled={!editable || saving}
              maxLength={500}
              className="min-h-24"
              placeholder="一句话介绍这个 Bot 擅长什么，会展示在协作名片上"
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
        </div>
        <ModalFooter>
          {dirty && editable ? (
            <span className="m-0 mr-auto self-center text-xs text-muted-foreground">有未保存的修改</span>
          ) : null}
          <Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button disabled={!canSubmit} onClick={() => void submit()}>
            保存修改
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
