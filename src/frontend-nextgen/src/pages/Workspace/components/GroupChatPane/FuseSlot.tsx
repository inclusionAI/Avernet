import type { GroupView, SessionView } from '@/domain/collaboration';
import { useFuseStore } from '@/stores/fuseStore';
import { useEffect, useState } from 'react';
import { FuseChatPanel } from './FuseChatPanel';
import { FuseFloatButton } from './FuseFloatButton';

interface FuseSlotProps {
  group: GroupView | null;
  session: SessionView | null;
  sessionId: string | null;
  /** 当前查看身份名称，用于融合消息区展示明确的发送者。 */
  viewerName?: string;
}

/** 融合模式插槽：悬浮按钮 + 问答面板 + 未读红点逻辑。 */
export function FuseSlot({ group, session, sessionId, viewerName }: FuseSlotProps) {
  const [open, setOpen] = useState(false);
  const unreadSessionIds = useFuseStore((s) => s.unreadSessionIds);
  const setUnreadSession = useFuseStore((s) => s.setUnreadSession);

  useEffect(() => {
    if (open && sessionId && unreadSessionIds[sessionId]) setUnreadSession(sessionId, false);
  }, [open, sessionId, unreadSessionIds, setUnreadSession]);

  return (
    <>
      <FuseFloatButton onClick={() => setOpen(true)} sessionId={sessionId} />
      <FuseChatPanel
        group={group}
        session={session}
        sessionId={sessionId}
        viewerName={viewerName}
        open={open}
        onOpenChange={setOpen}
      />
    </>
  );
}
