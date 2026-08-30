import type { GroupView } from '@/domain/collaboration';
import { useFuseStore } from '@/stores/fuseStore';
import { useEffect, useState } from 'react';
import { FuseChatPanel } from './FuseChatPanel';
import { FuseFloatButton } from './FuseFloatButton';

interface FuseSlotProps {
  group: GroupView | null;
  sessionId: string | null;
}

/** 融合模式插槽：悬浮按钮 + 问答面板 + 未读红点逻辑。 */
export function FuseSlot({ group, sessionId }: FuseSlotProps) {
  const [open, setOpen] = useState(false);
  const unreadSessionIds = useFuseStore((s) => s.unreadSessionIds);
  const setUnreadSession = useFuseStore((s) => s.setUnreadSession);

  useEffect(() => {
    if (open && sessionId && unreadSessionIds[sessionId]) setUnreadSession(sessionId, false);
  }, [open, sessionId, unreadSessionIds, setUnreadSession]);

  return (
    <>
      <FuseFloatButton onClick={() => setOpen(true)} sessionId={sessionId} />
      <FuseChatPanel group={group} sessionId={sessionId} open={open} onOpenChange={setOpen} />
    </>
  );
}
