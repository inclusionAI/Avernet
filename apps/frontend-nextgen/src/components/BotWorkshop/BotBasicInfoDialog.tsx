import type { BotDomain } from '@/domain/botWorkshop';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Textarea } from '@/components/ui/Textarea';
import { useEffect, useState } from 'react';
import { AvatarSettings } from './BotAvatar/AvatarSettings';

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

  useEffect(() => {
    if (!open) return;
    setName(bot.name);
    setDescription(bot.description ?? '');
  }, [bot.description, bot.name, open]);

  const submit = async () => {
    await onSave({ name: name.trim(), description: description.trim() });
    onOpenChange(false);
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="md">
        <ModalHeader>
          <ModalTitle>编辑 Bot 信息</ModalTitle>
        </ModalHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <p className="m-0 text-sm font-medium">头像</p>
            <AvatarSettings botId={bot.id} editable={editable} onSaved={onAvatarSaved} />
          </div>
          <label className="block space-y-2 text-sm font-medium">
            名称
            <Input
              value={name}
              disabled={!editable || saving}
              maxLength={100}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label className="block space-y-2 text-sm font-medium">
            描述
            <Textarea
              value={description}
              disabled={!editable || saving}
              maxLength={500}
              placeholder="请输入 Bot 描述"
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
        </div>
        <ModalFooter>
          <Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button disabled={!editable || saving || !name.trim()} onClick={() => void submit()}>
            保存
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
