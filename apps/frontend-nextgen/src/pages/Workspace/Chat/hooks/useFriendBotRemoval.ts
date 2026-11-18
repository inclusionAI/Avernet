// 好友 Bot 移除动线(移除好友 PRD):确认弹窗状态 + DELETE friend-connection +
// 本地状态清理(该 Bot 会话缓存/范围/检索/展开位)+ 好友目录重拉 + 选中清除。
// 口径:移除为静默、不可逆、只作用于好友关系维度(R2–R4);历史单聊会话仅前端隐藏
// (数据保留,R8),由好友行从目录消失承载,不调用会话删除;移除后如正选中该好友
// 或其会话,清除选中回到默认态(R9)。
import { deleteFriendConnection } from '@/services/backendApi/collaboration/collaborationFriendConnectionController';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useConversationStore } from '@/stores/conversationStore';
import { notifySuccess } from '@/components/ui/notify';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useState } from 'react';

export interface FriendBotRemovalModel {
  /** 确认弹窗目标 Bot(null = 弹窗关闭)。 */
  target: ChatBotView | null;
  /** 移除请求在途(确认按钮 loading;失败保持弹窗打开由调用方配合受控 open 实现)。 */
  busy: boolean;
  /** 打开移除确认弹窗(侧栏「移除好友」入口点击)。 */
  open(bot: ChatBotView): void;
  /** 取消/关闭确认弹窗(确认在途时不响应,与在途移除结果竞争)。 */
  cancel(): void;
  /** 确认移除:调 DELETE 接口(主体为登录用户,目标由 query 指定),成功后清理本地
   *  状态并重拉好友目录;失败保持弹窗打开,错误提示走统一收敛。 */
  confirm(): Promise<void>;
}

/** 好友 Bot 移除动线 Hook:弹窗状态与移除执行;目录重拉经 retryFriend 注入(整目录重拉)。 */
export function useFriendBotRemoval({ retryFriend }: { retryFriend(): void }): FriendBotRemovalModel {
  const [target, setTarget] = useState<ChatBotView | null>(null);
  const [busy, setBusy] = useState(false);

  const open = useCallback((bot: ChatBotView) => {
    setTarget(bot);
  }, []);

  const cancel = useCallback(() => {
    setTarget((current) => (current ? null : current));
  }, []);

  const confirm = useCallback(async () => {
    if (!target || busy) return;
    setBusy(true);
    try {
      await deleteFriendConnection({ target_actor_type: 'bot', target_actor_id: target.botId });
      const store = useConversationStore.getState();
      // 本地状态清理:该 Bot 的好友会话缓存/范围记忆/检索条件与展开位(重加好友从默认态开始)。
      store.removeFriendBotState(target.botId);
      // 正选中该好友或其会话时清除选中,回到会话列表默认态(R9)。
      if (store.selectedBotId === target.botId && store.selectedSection === 'friend') {
        store.selectConversation({
          botId: null,
          section: null,
          origin: 'mine',
          scope: 'all',
          friendUserId: null,
          sessionId: null,
        });
      }
      setTarget(null);
      // 好友目录重拉:该 Bot 从「好友 Bot」分组消失,历史会话随行消失(前端隐藏,R8)。
      retryFriend();
      notifySuccess('已移除好友');
    } catch (error) {
      // 失败不关弹窗(受控 open 由 target 驱动);错误提示经统一收敛,协议层已提示的跳过。
      safeReportError(error, { title: '移除好友失败' });
    } finally {
      setBusy(false);
    }
  }, [target, busy, retryFriend]);

  return { target, busy, open, cancel, confirm };
}
