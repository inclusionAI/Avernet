// 移除好友二次确认弹窗(用户身份视角,PRD v1.5 AC-02/AC-03)。
// 文案必须完整告知后果:重新申请、分组消失、历史单聊会话隐藏及重新添加后恢复;
// 同时说明作用边界(不影响 Bot 公开配置与已共同所在的协作群/会话成员资格)。
import { ConfirmDialog } from '@/components/ui';

export interface FriendRemovalConfirmDialogProps {
  open: boolean;
  /** 待移除好友 Bot 的显示名;缺省用通用文案。 */
  botName?: string;
  loading?: boolean;
  onConfirm(): void;
  onCancel(): void;
}

export function FriendRemovalConfirmDialog({
  open,
  botName,
  loading = false,
  onConfirm,
  onCancel,
}: FriendRemovalConfirmDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      title="移除好友"
      description={
        // AlertDialogDescription 渲染为 <p>,描述内容只能用行内节点(span),避免非法 DOM 嵌套。
        <span className="block space-y-2">
          <span className="block">{botName ? `确定要移除好友「${botName}」吗？` : '确定要移除该好友吗？'}</span>
          <span className="block">移除后：</span>
          <span className="block pl-4">· 如需继续协作，须重新发起添加好友申请。</span>
          <span className="block pl-4">· 该 Bot 将从「好友 Bot」分组消失。</span>
          <span className="block pl-4">
            · 历史单聊会话将从会话列表隐藏且无法再发起新会话；重新添加好友后恢复显示。
          </span>
          <span className="block pl-4">
            · 不影响你通过「我管理的 Bot」或「我协作的 Bot」获得的关系，也不影响 Bot 的公开配置和已共同所在的协作群。
          </span>
        </span>
      }
      confirmText="移除好友"
      confirmVariant="destructive"
      loading={loading}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}