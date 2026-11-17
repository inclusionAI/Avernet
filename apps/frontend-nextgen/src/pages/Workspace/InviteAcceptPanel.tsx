import { MessageViewScopeField } from '@/components/MessageViewScope';
import { Button, Empty, Spin } from '@/components/ui';
import { DEFAULT_MESSAGE_VIEW_SCOPE } from '@/domain/collaboration/messageViewScope';
import type { MessageViewScope } from '@/domain/collaboration/types';
import { serializeWorkspaceRoute } from '@/domain/workspaceRoute';
import { useInviteAccept } from '@/pages/Workspace/hooks/useInviteAccept';
import { Sparkles } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

/**
 * InviteAcceptPanel —— 邀请接受落地页（纯 view）。
 *
 * Action 全部经 `useInviteAccept` 下发（mount 时校验 token、`accept()` 提交接受）；
 * 组件不直接 import `invitationService`，保持组件层级约束。
 * 已加入 / 成功加入后跳转到 `/workspace/collaboration[?group=#groupId]`。
 */
export function InviteAcceptPanel() {
  const params = useParams<{ token?: string; type?: string }>();
  const navigate = useNavigate();
  const token = params.token ?? '';
  // 邀请类型来自路径段：/workspace/invite/groups/:token | /workspace/invite/sessions/:token
  const inviteType = params.type === 'groups' ? 'group' : params.type === 'sessions' ? 'session' : undefined;
  const {
    status, friendlyMessage, canRetry, targetType, targetId, groupId, alreadyJoined,
    currentIdentityId, humanIdentityStatus, humanIdentityError, accept, resetToConfirm, retryPreview,
  } = useInviteAccept(token);
  // 路径段可被编辑；预检后以 BCS 验签得到的类型为准。
  const verifiedInviteType = targetType ?? inviteType;
  const targetLabel = verifiedInviteType === 'session' ? '该会话' : '该协作群';
  // 确认页可选消息视角，accept 时透传给邀请接受接口。
  const [viewScope, setViewScope] = useState<MessageViewScope>(DEFAULT_MESSAGE_VIEW_SCOPE);

  // accepted 状态下做一次导航副作用（写在 effect 里避免渲染中途触发路由跳转）。
  const navigatedToken = useRef<string | null>(null);
  useEffect(() => setViewScope(DEFAULT_MESSAGE_VIEW_SCOPE), [token]);
  useEffect(() => {
    if (status !== 'accepted' || navigatedToken.current === token || !currentIdentityId) return;
    navigatedToken.current = token;
    if (targetType === 'group' && targetId) {
      toast.info(alreadyJoined ? '已加入该协作群' : '已加入协作群');
      navigate(`/workspace/collaboration?${serializeWorkspaceRoute({ currentIdentityId, groupId: targetId })}`, { replace: true });
      return;
    }
    if (targetType === 'session' && targetId) {
      toast.info(alreadyJoined ? '已加入该会话' : '已加入会话');
      navigate(`/workspace/collaboration?${serializeWorkspaceRoute({ currentIdentityId, groupId, sessionId: targetId })}`, { replace: true });
      return;
    }
    if (alreadyJoined) {
      toast.info('已加入该协作邀请');
      navigate('/workspace/collaboration', { replace: true });
      return;
    }
    // session 目标但暂未解析到所属群，或 409 无 target 信息：回到协作群列表。
    toast.info('已加入协作邀请');
    navigate('/workspace/collaboration', { replace: true });
  }, [status, alreadyJoined, currentIdentityId, groupId, navigate, targetId, targetType, token]);

  if (status === 'loading') {
    return (
      <div className="flex min-h-64 items-center justify-center py-14">
        <Spin tip="正在校验协作邀请链接…" />
      </div>
    );
  }

  if (status === 'invalid') {
    return (
      <div className="flex min-h-64 items-center justify-center py-14">
        <Empty
          title="邀请链接无效"
          description={friendlyMessage ?? '该邀请已失效，请让群主重新生成。'}
          icon={<Sparkles className="h-5 w-5" />}
        />
      </div>
    );
  }

  if (status === 'preview-error') {
    return (
      <div className="flex min-h-64 items-center justify-center py-14">
        <Empty
          title="邀请校验失败"
          description={friendlyMessage ?? '邀请校验失败，请稍后重试。'}
          icon={<Sparkles className="h-5 w-5" />}
          action={canRetry ? <Button size="sm" onClick={retryPreview}>重新校验</Button> : undefined}
        />
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div className="flex min-h-64 items-center justify-center py-14">
        <Empty
          title="加入协作群失败"
          description={friendlyMessage ?? '加入协作群失败，请稍后重试。'}
          icon={<Sparkles className="h-5 w-5" />}
          action={
            <Button size="sm" onClick={resetToConfirm}>
              返回确认
            </Button>
          }
        />
      </div>
    );
  }

  if (status === 'accepted' && !currentIdentityId && humanIdentityStatus !== 'loading') {
    return (
      <div className="flex min-h-64 items-center justify-center py-14">
        <Empty
          title="当前用户身份未就绪"
          description={humanIdentityError ?? '无法确认当前用户身份，请刷新页面后重试。'}
          icon={<Sparkles className="h-5 w-5" />}
        />
      </div>
    );
  }

  if (status === 'accepting' || status === 'accepted') {
    return (
      <div className="flex min-h-64 items-center justify-center py-14">
        <Spin
          tip={
            status === 'accepted'
              ? verifiedInviteType === 'session' ? '正在打开会话…' : '正在打开协作群…'
              : verifiedInviteType === 'session'
              ? '正在加入会话…'
              : '正在加入协作群…'
          }
        />
      </div>
    );
  }

  // status === 'confirm'
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-4 px-6 py-14 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
        <Sparkles className="h-5 w-5" />
      </div>
      <h1 className="m-0 text-base font-semibold text-foreground">是否确认加入{targetLabel}</h1>
      <p className="m-0 text-sm leading-6 text-muted-foreground">
        {verifiedInviteType === 'session'
          ? '加入后将作为成员参与该会话的对话与资源协作，可随时退出。'
          : '加入后将作为成员参与该协作群的对话与资源协作，可随时退出。'}
      </p>
      <div className="w-full">
        <MessageViewScopeField value={viewScope} onChange={setViewScope} />
      </div>
      <Button
        size="lg"
        onClick={() => {
          Tracert.click(Points.invite.accept);
          void accept(viewScope);
        }}
      >
        确认加入
      </Button>
    </div>
  );
}

export default InviteAcceptPanel;
