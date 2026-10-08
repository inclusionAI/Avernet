import { Button, Card } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { GroupView, IdentityView, SessionView } from '@/domain/collaboration';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { Link as LinkIcon, LogOut, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { EditableName } from './EditableName';
import { ManagePanelHeader } from './ManagePanelHeader';
import { MemberList } from './MemberList';
import { ShareDialog } from './ShareDialog';

export interface SessionManagePanelProps {
  session: SessionView;
  groupName?: string;
  groupKind: GroupView['kind'];
  canManage: PolicyResult;
  activeIdentity: IdentityView | null;
  authenticatedUserId?: string | null;
  authenticatedUserName?: string | null;
  candidates: IdentityView[];
  onClose: () => void;
  onRename: (sessionId: string, title: string) => Promise<boolean>;
  onDelete: (sessionId: string) => Promise<boolean>;
  onLeaveSession: (actorId: string) => Promise<boolean>;
  onAddMember: (actorId: string) => Promise<boolean>;
  onRemoveMember: (actorId: string) => Promise<boolean>;
  onShare: () => Promise<DomainResult<{ invitationUrl: string }>>;
}

const GROUP_KIND_LABEL: Record<GroupView['kind'], string> = {
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协作',
};

export function SessionManagePanel(props: SessionManagePanelProps) {
  const {
    session,
    groupName,
    canManage,
    activeIdentity,
    authenticatedUserId,
    authenticatedUserName,
    onClose,
    onRename,
    onDelete,
    onLeaveSession,
  } = props;
  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  // 会话级权限判断：driver/manager 或会话创建者可删除会话，其他成员可退出会话。
  // 参考 open-claw：isCreator（createdBy === 当前身份）|| isDriver/Manager（participants role）。
  const isCreator = !!session.createdBy && !!activeIdentity && session.createdBy === activeIdentity.id;
  const isSessionDriverOrManager =
    isCreator ||
    session.participants.some((p) => p.actorId === activeIdentity?.id && (p.role === 'driver' || p.role === 'manager'));

  const handleAddMany = async (actorIds: string[]) => {
    let success = 0;
    for (const actorId of actorIds) {
      if (await props.onAddMember(actorId)) success += 1;
    }
    return success;
  };

  const handleShare = async () => {
    setShareOpen(true);
    setSharing(true);
    const res = await props.onShare();
    setSharing(false);
    if (res.ok) setShareUrl(res.data.invitationUrl);
  };

  return (
    <aside className="flex h-full flex-col bg-background">
      <ManagePanelHeader
        title="会话管理"
        subtitle={groupName ? `${groupName} · ${session.title}` : session.title}
        statusLabel={isSessionDriverOrManager ? '可管理' : '可查看'}
        onClose={onClose}
      />

      <div className="app-scrollbar flex-1 overflow-y-auto p-4">
        <div className="space-y-3">
          <Card className="rounded-lg bg-card p-3">
            <p className="m-0 mb-2 text-sm font-medium text-foreground">基础信息</p>
            <p className="m-0 mb-1 text-xs text-muted-foreground">会话标题</p>
            <EditableName
              value={session.title}
              canEdit={isSessionDriverOrManager}
              editLabel="编辑会话标题"
              onSave={(next) => void onRename(session.sessionId, next)}
            />
            <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-3 text-xs">
              <div>
                <p className="m-0 text-muted-foreground">类型</p>
                <p className="m-0 mt-1 font-medium text-foreground">{GROUP_KIND_LABEL[props.groupKind]}</p>
              </div>
              <div>
                <p className="m-0 text-muted-foreground">成员数量</p>
                <p className="m-0 mt-1 font-medium text-foreground">
                  {session.participantCount || session.participants.length}
                </p>
              </div>
            </div>
            <div className="mt-3 space-y-3 text-xs">
              <div>
                <p className="m-0 text-muted-foreground">群 ID</p>
                <p className="m-0 mt-1 break-all font-mono text-xs text-foreground">{session.groupId}</p>
              </div>
              <div>
                <p className="m-0 text-muted-foreground">会话 ID</p>
                <p className="m-0 mt-1 break-all font-mono text-xs text-foreground">{session.sessionId}</p>
              </div>
            </div>
          </Card>

          <Card className="rounded-lg bg-card p-3">
            <MemberList
              headerLabel="会话成员"
              participants={session.participants}
              participantCount={session.participantCount}
              activeIdentity={activeIdentity}
              authenticatedUserId={authenticatedUserId}
              authenticatedUserName={authenticatedUserName}
              canManage={isSessionDriverOrManager}
              disabledReason={canManage.disabledReason}
              emptyText="暂无成员"
              addLabel="添加成员"
              groupKind={props.groupKind}
              showMode
              badgePolicy="session"
              onAddMany={handleAddMany}
              onRemove={props.onRemoveMember}
            />
          </Card>

          <Card className="rounded-lg bg-card p-3">
            <p className="m-0 mb-1 text-sm font-medium text-foreground">操作</p>
            <div className="flex flex-col divide-y divide-border">
              <Button
                variant="ghost"
                onClick={() => void handleShare()}
                className="h-auto w-full justify-start px-2 py-2 text-left text-xs text-primary hover:bg-primary/10 hover:text-primary"
              >
                <LinkIcon className="h-4 w-4 shrink-0" />
                <span className="flex flex-col items-start">
                  <span>分享会话</span>
                  <span className="text-xs font-normal text-muted-foreground">人类角色可以通过链接加入会话</span>
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
                    className="h-auto w-full justify-start px-2 py-2 text-left text-xs text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="h-4 w-4 shrink-0" />
                    <span className="flex flex-col items-start">
                      <span>删除会话</span>
                      <span className="text-xs font-normal text-muted-foreground">此操作不可恢复，请谨慎操作</span>
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
                    className="h-auto w-full justify-start px-2 py-2 text-left text-xs text-destructive hover:bg-destructive/10"
                  >
                    <LogOut className="h-4 w-4 shrink-0" />
                    <span className="flex flex-col items-start">
                      <span>退出会话</span>
                      <span className="text-xs font-normal text-muted-foreground">退出后将不再接收该会话消息</span>
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
