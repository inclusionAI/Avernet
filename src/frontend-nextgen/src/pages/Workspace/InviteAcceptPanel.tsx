import { Button, Empty, Spin } from '@/components/ui';
import { useInviteAccept } from '@/pages/Workspace/hooks/useInviteAccept';
import { Sparkles } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { toast } from 'sonner';

/**
 * InviteAcceptPanel —— 邀请接受落地页（纯 view）。
 *
 * Action 全部经 `useInviteAccept` 下发（mount 时校验 token、`accept()` 提交接受）；
 * 组件不直接 import `invitationService`，保持组件层级约束。
 * 已加入 / 成功加入后跳转到 `/workspace?tab=group[&group=#groupId]`。
 */
export function InviteAcceptPanel() {
  const params = useParams<{ token?: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = params.token ?? '';
  const typeParam = searchParams.get('type');
  const inviteType = typeParam === 'group' || typeParam === 'session' ? typeParam : undefined;
  const { status, friendlyMessage, targetType, targetId, groupId, alreadyJoined, accept, resetToConfirm } =
    useInviteAccept(token);
  const targetLabel = inviteType === 'session' ? '该会话' : '该协作群';

  // accepted 状态下做一次导航副作用（写在 effect 里避免渲染中途触发路由跳转）。
  const [navigated, setNavigated] = useState(false);
  useEffect(() => {
    if (status !== 'accepted' || navigated) return;
    setNavigated(true);
    if (targetType === 'group' && targetId) {
      toast.info(alreadyJoined ? '已加入该协作群' : '已加入协作群');
      navigate(`/workspace?tab=group&group=${encodeURIComponent(targetId)}`);
      return;
    }
    if (targetType === 'session' && targetId) {
      toast.info(alreadyJoined ? '已加入该会话' : '已加入会话');
      const groupParam = groupId ? `group=${encodeURIComponent(groupId)}&` : '';
      navigate(`/workspace?tab=group&${groupParam}session=${encodeURIComponent(targetId)}`);
      return;
    }
    if (alreadyJoined) {
      toast.info('已加入该协作邀请');
      navigate('/workspace?tab=group');
      return;
    }
    // session 目标但暂未解析到所属群，或 409 无 target 信息：回到协作群列表。
    toast.info('已加入协作邀请');
    navigate('/workspace?tab=group');
  }, [status, alreadyJoined, groupId, navigated, navigate, targetId, targetType]);

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

  if (status === 'accepting' || status === 'accepted') {
    return (
      <div className="flex min-h-64 items-center justify-center py-14">
        <Spin
          tip={
            status === 'accepted'
              ? '加入成功，正在跳转…'
              : inviteType === 'session'
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
        {inviteType === 'session'
          ? '加入后将作为成员参与该会话的对话与资源协作，可随时退出。'
          : '加入后将作为成员参与该协作群的对话与资源协作，可随时退出。'}
      </p>
      <Button
        size="lg"
        onClick={() => {
          void accept();
        }}
      >
        确认加入
      </Button>
    </div>
  );
}

export default InviteAcceptPanel;
