import type { HumanIdentity } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import type { FriendRequestActor } from '@/domain/collaborationSquare/types';
import { UserRoundPlus } from 'lucide-react';
import { useState } from 'react';
import { FriendRequestsDialog } from '../FriendRequestsDialog';

interface FriendRequestsButtonProps {
  actor: FriendRequestActor | null;
  actorName?: string;
  human: HumanIdentity | null;
  onChanged: () => void;
}
/** 父层按登录人+发现身份加 key，切身份关闭旧弹窗并取消旧代次请求。 */
export function FriendRequestsButton({ actor, actorName, human, onChanged }: FriendRequestsButtonProps) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="secondary" className="shrink-0" disabled={!actor || !human}
        leftIcon={<UserRoundPlus className="size-4" aria-hidden />} onClick={() => setOpen(true)}>
        好友申请
      </Button>
      {open && actor && human ? (
        <FriendRequestsDialog actor={actor} actorName={actorName || actor.id} human={human}
          onClose={() => setOpen(false)} onChanged={onChanged} />
      ) : null}
    </>
  );
}
