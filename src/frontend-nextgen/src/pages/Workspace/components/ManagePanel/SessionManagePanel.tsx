import { Badge, Button, Card, Input } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { IdentityView, SessionView } from '@/domain/collaboration';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { Link as LinkIcon, LogOut, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { ManagePanelHeader } from './ManagePanelHeader';
import { MemberList } from './MemberList';
import { ShareDialog } from './ShareDialog';

export interface SessionManagePanelProps {
  session: SessionView;
  groupName?: string;
  canManage: PolicyResult;
  activeIdentity: IdentityView | null;
  candidates: IdentityView[];
  onClose: () => void;
  onRename: (sessionId: string, title: string) => Promise<boolean>;
  onDelete: (sessionId: string) => Promise<boolean>;
  onLeaveSession: (actorId: string) => Promise<boolean>;
  onAddMember: (actorId: string) => Promise<boolean>;
  onRemoveMember: (actorId: string) => Promise<boolean>;
  onShare: () => Promise<DomainResult<{ invitationUrl: string }>>;
}

export function SessionManagePanel(props: SessionManagePanelProps) {
  const { session, groupName, canManage, activeIdentity, onClose, onRename, onDelete, onLeaveSession } = props;
  const [title, setTitle] = useState(session.title);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  // 会话级权限判断：driver/manager 可删除会话，其他成员可退出会话。
  const isSessionDriverOrManager = session.participants.some(
    (p) => p.actorId === activeIdentity?.id && (p.role === 'driver' || p.role === 'manager'),
  );

  const handleAddMany = async (actorIds: string[]) => {
    let success = 0;
    for (const actorId of actorIds) {
      if (await props.onAddMember(actorId)) success += 1;
    }
    return success;
  };

  const handleSaveTitle = () => {
    const next = title.trim();
    if (!next || next === session.title) return;
    void onRename(session.sessionId, next);
  };

  const handleShare = async () => {
    setShareOpen(true);
    setSharing(true);
    const res = await props.onShare();
    setSharing(false);
    if (res.ok) setShareUrl(res.data.invitationUrl);
  };

  return (
    <aside className="flex h-full flex-col bg-white">
      <ManagePanelHeader
        title="会话管理"
        description="查看会话基础信息与参与成员，维护当前会话的协作范围。"
        subtitle={groupName ? `${groupName} · ${session.title}` : session.title}
        statusLabel={isSessionDriverOrManager ? '可管理' : '可查看'}
        onClose={onClose}
      />

      <div className="app-scrollbar flex-1 overflow-y-auto p-5">
        <div className="space-y-4">
          <Card className="rounded-2xl bg-white p-4 shadow-sm">
            <div className="mb-4 flex items-center justify-between gap-2">
              <Badge tone="primary">{session.kind === 'service_invocation' ? '服务调用' : '聊天会话'}</Badge>
              <Badge tone={session.status === 'running' ? 'success' : 'neutral'}>
                {session.status === 'running' ? '进行中' : '已完成'}
              </Badge>
            </div>
            <label className="block">
              <span className="mb-1.5 block text-xs text-[var(--color-muted)]">会话标题</span>
              <div className="flex gap-2">
                <Input value={title} onChange={(event) => setTitle(event.target.value)} />
                <Button variant="secondary" size="sm" onClick={handleSaveTitle}>
                  保存
                </Button>
              </div>
            </label>
            <div className="mt-4 rounded-xl bg-[var(--color-panel-strong)] px-3 py-2.5">
              <p className="m-0 text-[11px] font-medium text-[var(--color-muted)]">成员数量</p>
              <p className="m-0 mt-1 text-sm font-medium text-[var(--color-fg)]">
                {session.participantCount || session.participants.length}
              </p>
            </div>
          </Card>

          <Card className="rounded-2xl bg-white p-4 shadow-sm">
            <p className="m-0 mb-3 text-sm font-semibold text-[var(--color-fg)]">会话成员管理</p>
            <MemberList
              participants={session.participants}
              participantCount={session.participantCount}
              activeIdentity={activeIdentity}
              canManage={isSessionDriverOrManager}
              disabledReason={canManage.disabledReason}
              emptyText="暂无成员"
              addLabel="添加会话成员"
              onAddMany={handleAddMany}
              onRemove={props.onRemoveMember}
            />
          </Card>

          <Card className="rounded-2xl bg-white p-4 shadow-sm">
            <p className="m-0 mb-3 text-sm font-semibold text-[var(--color-fg)]">操作</p>
            <div className="flex flex-col gap-3">
              <Button
                variant="ghost"
                onClick={() => void handleShare()}
                className="h-auto w-full justify-start rounded-xl border border-[var(--color-primary)]/25 bg-white px-4 py-2.5 text-left text-sm text-[var(--color-primary)] hover:bg-[var(--color-primary-soft)] hover:text-[var(--color-primary)]"
              >
                <LinkIcon className="h-4 w-4 shrink-0" />
                <span className="flex flex-col items-start">
                  <span>分享会话</span>
                  <span className="text-[11px] font-normal text-[var(--color-muted)]">
                    生成会话邀请链接，供成员通过链接加入
                  </span>
                </span>
              </Button>
              {isSessionDriverOrManager ? (
                <ConfirmDialog
                  title="删除会话"
                  description="删除后会话内的消息与文件将无法恢复。"
                  confirmText="确认删除"
                  confirmVariant="destructive"
                  onConfirm={() => void onDelete(session.sessionId)}
                >
                  <Button
                    variant="ghost"
                    className="h-auto w-full justify-start rounded-xl border border-[var(--color-error-soft)] bg-white px-4 py-2.5 text-left text-sm text-[var(--color-error)] hover:bg-[var(--color-error-soft)]/60"
                  >
                    <Trash2 className="h-4 w-4 shrink-0" />
                    <span className="flex flex-col items-start">
                      <span>删除会话</span>
                      <span className="text-[11px] font-normal text-[var(--color-muted)]">
                        此操作不可恢复，请谨慎操作
                      </span>
                    </span>
                  </Button>
                </ConfirmDialog>
              ) : (
                <ConfirmDialog
                  title="退出会话"
                  description="退出后将不再接收该会话消息。"
                  confirmText="确认退出"
                  confirmVariant="destructive"
                  disabled={!activeIdentity}
                  onConfirm={() => {
                    if (activeIdentity) void onLeaveSession(activeIdentity.id);
                    else toast.error('未选择当前身份');
                  }}
                >
                  <Button
                    variant="ghost"
                    disabled={!activeIdentity}
                    className="h-auto w-full justify-start rounded-xl border border-[var(--color-error-soft)] bg-white px-4 py-2.5 text-left text-sm text-[var(--color-error)] hover:bg-[var(--color-error-soft)]/60"
                  >
                    <LogOut className="h-4 w-4 shrink-0" />
                    <span className="flex flex-col items-start">
                      <span>退出会话</span>
                      <span className="text-[11px] font-normal text-[var(--color-muted)]">
                        退出后将不再接收该会话消息
                      </span>
                    </span>
                  </Button>
                </ConfirmDialog>
              )}
            </div>
          </Card>
        </div>
      </div>

      <ShareDialog
        open={shareOpen}
        title="会话"
        inviting={sharing}
        invitationUrl={shareUrl}
        onClose={() => setShareOpen(false)}
      />
    </aside>
  );
}
