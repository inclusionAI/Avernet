import { Button, Empty, Spin } from '@/components/ui';
import { useBcnChatDetailRedirect } from '@/pages/Workspace/hooks/useBcnChatDetailRedirect';
import { useNavigate } from 'react-router-dom';

/**
 * BcnChatDetail —— BCN 协作会话外链落地页（纯 view）。
 *
 * /workspace/bcn/chat/detail?id={groupId}&bot_uuid={currentUser}&session={sessionId}
 * 参数解析、参与方式判定（固定群成员/仅参与临时会话）与重定向全部经
 * `useBcnChatDetailRedirect` 下发；组件不直接 import service，保持分层约束。
 * 对齐 InviteAcceptPanel 落地页范式。
 */
export function BcnChatDetail() {
  const { status } = useBcnChatDetailRedirect();
  const navigate = useNavigate();

  if (status === 'invalid') {
    return (
      <div className="flex min-h-64 items-center justify-center py-14">
        <Empty
          title="链接参数不完整"
          description="缺少协作群或会话标识（id / session），无法定位到目标会话。"
          action={
            <Button variant="secondary" size="sm" onClick={() => navigate('/workspace')}>
              返回对话协作
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex min-h-64 items-center justify-center py-14">
      <Spin tip="正在打开协作会话…" />
    </div>
  );
}

export default BcnChatDetail;
