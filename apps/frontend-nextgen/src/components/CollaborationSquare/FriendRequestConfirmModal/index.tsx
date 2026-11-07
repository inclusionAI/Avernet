import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalTitle } from '@/components/ui/Modal';
import type { FriendRequestActor, PublicBot } from '@/domain/collaborationSquare/types';

interface FriendRequestConfirmModalProps {
  bot: PublicBot;
  actor: FriendRequestActor;
  actorName: string;
  onClose: () => void;
  onConfirm: () => void;
}

export function FriendRequestConfirmModal({ bot, actor, actorName, onClose, onConfirm }: FriendRequestConfirmModalProps) {
  const isHuman = actor.type === 'human';
  return (
    <Modal open onOpenChange={(open) => !open && onClose()}>
      <ModalContent className="max-w-[400px] gap-4 py-6 text-left">
        <ModalTitle className="pr-8 text-lg leading-7">Bot好友申请</ModalTitle>
        <ModalDescription className="break-words text-left text-sm leading-6 text-foreground">
          以{isHuman ? '用户' : 'Bot'}{' '}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.9em]">{actorName}</code>
          {' '}身份申请添加Bot{' '}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.9em]">{bot.name}</code>
          {' '}为好友
        </ModalDescription>
        <p className="m-0 break-words text-left text-xs leading-5 text-muted-foreground">
          {isHuman
            ? 'Bot Owner用户通过申请后，该申请身份可与该Bot单聊。'
            : 'Bot Owner用户通过申请后，该申请身份仅可与该Bot发起群聊，不支持直接单聊。'}
        </p>
        <div className="mt-1 flex justify-center gap-3">
          <Button variant="secondary" onClick={onClose}>取消</Button>
          <Button onClick={onConfirm}>发送申请</Button>
        </div>
      </ModalContent>
    </Modal>
  );
}
