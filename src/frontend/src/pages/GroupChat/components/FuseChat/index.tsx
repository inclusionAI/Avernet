/**
 * FuseSlot - 融合模式悬浮问答插槽（内部专属）
 *
 * 封装融合模式的全部 UI 与状态：悬浮按钮 + 聊天面板 + 弹窗开关 + 未读红点逻辑。
 * 原本散落在 GroupChatPage 的 fuseOpen / 未读红点 effect / fuseStore 读取一并收入此处，
 * 使核心 GroupChatPage 仅通过 AppExt.slots.fuseChat 注入消费，组件代码不进开源闭包。
 */

import type { FuseSlotProps } from '@/shell/types';
import React, { useEffect, useState } from 'react';
import { useFuseStore } from '@/stores/groupchat/fuseStore';
import FuseChatPanel from './FuseChatPanel';
import FuseFloatButton from './FuseFloatButton';

const FuseSlot: React.FC<FuseSlotProps> = ({ group, activeSessionId }) => {
  const [fuseOpen, setFuseOpen] = useState(false);

  // 智能问答未读红点逻辑（按 session 维度）
  // 未读标记在 useFuse.submitQuestion 完成时写入，此处仅负责打开面板时清除当前 session 未读。
  const unreadSessionIds = useFuseStore((s) => s.unreadSessionIds);
  const setUnreadSession = useFuseStore((s) => s.setUnreadSession);

  // 打开弹窗 → 清除当前 session 未读
  useEffect(() => {
    if (fuseOpen && activeSessionId && unreadSessionIds[activeSessionId]) {
      setUnreadSession(activeSessionId, false);
    }
  }, [fuseOpen, activeSessionId, unreadSessionIds, setUnreadSession]);

  return (
    <>
      <FuseFloatButton
        onClick={() => setFuseOpen(true)}
        sessionId={activeSessionId}
      />
      <FuseChatPanel
        group={group}
        sessionId={activeSessionId}
        open={fuseOpen}
        onOpenChange={setFuseOpen}
      />
    </>
  );
};

export default FuseSlot;
