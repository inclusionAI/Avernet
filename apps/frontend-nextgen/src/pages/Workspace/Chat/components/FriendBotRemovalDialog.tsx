// 移除好友确认弹窗(移除好友 PRD R5/R7):危险操作二次确认;疑问句独立成行、两项后果
// 分行列示、目标名称行内代码标签(2026-11-18 验收反馈:文案精简、关键信息分行)。
// 自 ConversationPage 拆出(TC-G005 组件行数门禁),与移除动线 Hook 配对由侧栏装配。
import { ConfirmDialog, InlineCode } from '@/components/ui';
import type { FriendBotRemovalModel } from '../hooks/useFriendBotRemoval';

/** 移除好友确认弹窗:目标为空(弹窗关闭)时零渲染。 */
export function FriendBotRemovalDialog({ removal }: { removal: FriendBotRemovalModel }) {
  if (!removal.target) return null;
  return (
    <ConfirmDialog
      open
      title="移除好友"
      description={
        <>
          <span className="block">
            确定移除好友
            <InlineCode className="mx-1">{removal.target.displayName}</InlineCode>
            吗？
          </span>
          <span className="mt-2 block">· 移除后如需继续协作，须重新发起添加好友申请</span>
          <span className="mt-1 block">· 历史单聊会话将从会话列表隐藏，重新添加好友后恢复</span>
        </>
      }
      confirmText="确认移除"
      confirmVariant="destructive"
      loading={removal.busy}
      onCancel={removal.cancel}
      onConfirm={removal.confirm}
    />
  );
}
