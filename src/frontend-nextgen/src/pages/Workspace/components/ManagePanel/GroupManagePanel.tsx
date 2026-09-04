import { Badge, Button, Card, Input, Switch } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { GroupView, IdentityView } from '@/domain/collaboration';
import type { DingTalkBindingState } from '@/services/workspace/channelBindingService';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { Link as LinkIcon, LogOut, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import type { GroupDingTalkConfig } from './DingTalkConfigPanel';
import { DingTalkConfigPanel } from './DingTalkConfigPanel';
import { ManagePanelHeader } from './ManagePanelHeader';
import { ManagePanelTabs } from './ManagePanelTabs';
import { MemberList } from './MemberList';
import { ShareDialog } from './ShareDialog';

export interface GroupManagePanelProps {
  group: GroupView;
  canManage: PolicyResult;
  candidates: IdentityView[];
  activeIdentity: IdentityView | null;
  onClose: () => void;
  onUpdate: (patch: {
    name?: string;
    visibility?: 'private' | 'public';
  }) => Promise<DomainResult<GroupView> | null> | void;
  onDissolve: () => void;
  onLeaveGroup: (actorId: string) => Promise<boolean>;
  onAddMember: (actorId: string) => Promise<boolean>;
  onRemoveMember: (actorId: string) => Promise<boolean>;
  onShare: () => Promise<DomainResult<{ invitationUrl: string }>>;
  onSaveDingTalk: (config: GroupDingTalkConfig) => Promise<boolean>;
  onToggleDingTalkActive: (active: boolean) => Promise<boolean>;
  onDeleteDingTalk: () => Promise<boolean>;
  dingTalkBinding: DingTalkBindingState;
  dingTalkLoading: boolean;
  advancedConfigEnabled: boolean;
}

const KIND_LABEL: Record<GroupView['kind'], string> = {
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协同',
};

export function GroupManagePanel(props: GroupManagePanelProps) {
  const { group, canManage, activeIdentity, onClose, onUpdate, onDissolve, onLeaveGroup } = props;
  const [tab, setTab] = useState<'basic' | 'advanced'>('basic');
  const [name, setName] = useState(group.name);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);

  const canManageGroup = canManage.allowed;
  // 当前身份是否为该群的直属成员（在 participants 名单中）。仅直属成员可「退出协作群」；
  // 非成员（仅可查看的访客/会话成员）不展示退出按钮，避免误以为自己是群成员。
  const isDirectMember = !!activeIdentity && group.participants.some((p) => p.actorId === activeIdentity.id);

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

  const handleSaveName = () => {
    const next = name.trim();
    if (!next || next === group.name) return;
    void onUpdate({ name: next });
  };

  const handleVisibilityChange = (checked: boolean) => {
    if (!canManageGroup) return;
    void onUpdate({ visibility: checked ? 'public' : 'private' });
  };

  return (
    <aside className="flex h-full flex-col bg-background">
      <ManagePanelHeader
        title="群管理"
        description="查看协作群基础信息与群成员，围绕同一任务现场保持协作一致。"
        subtitle={group.name}
        statusLabel={canManageGroup ? '可管理' : '可查看'}
        onClose={onClose}
      />

      <ManagePanelTabs<'basic' | 'advanced'>
        value={tab}
        options={[
          { value: 'basic', label: '基础信息' },
          ...(props.advancedConfigEnabled ? [{ value: 'advanced' as const, label: '高级配置' }] : []),
        ]}
        onChange={setTab}
      />

      <div className="app-scrollbar flex-1 overflow-y-auto p-4">
        {tab === 'basic' ? (
          <div className="space-y-3">
            <Card className="rounded-lg bg-card p-3 shadow-sm">
              <div className="mb-3 flex items-center justify-between gap-2">
                <Badge tone="primary">{KIND_LABEL[group.kind]}</Badge>
                <Badge tone={group.isPublic ? 'success' : 'neutral'}>{group.isPublic ? '公开群' : '私密群'}</Badge>
              </div>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-muted-foreground">群名称</span>
                <div className="flex gap-2">
                  <Input value={name} onChange={(event) => setName(event.target.value)} />
                  <Button variant="secondary" size="sm" onClick={handleSaveName}>
                    保存
                  </Button>
                </div>
              </label>
              <div className="mt-3 grid grid-cols-2 gap-2">
                <div className="rounded-lg bg-muted px-3 py-2">
                  <p className="m-0 text-[11px] font-medium text-muted-foreground">成员数量</p>
                  <p className="m-0 mt-1 text-sm font-medium text-foreground">
                    {group.participantCount || group.participants.length}
                  </p>
                </div>
                <div className="rounded-lg bg-muted px-3 py-2">
                  <p className="m-0 text-[11px] font-medium text-muted-foreground">创建时间</p>
                  <p className="m-0 mt-1 text-sm font-medium text-foreground">
                    {new Date(group.createdAt).toLocaleDateString()}
                  </p>
                </div>
              </div>
            </Card>

            <Card className="rounded-lg bg-card p-3 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="m-0 text-sm font-medium text-foreground">公开群</p>
                  <p className="m-0 mt-0.5 text-xs text-muted-foreground">公开群允许通过邀请链接加入。</p>
                </div>
                <Switch
                  checked={group.isPublic}
                  onCheckedChange={handleVisibilityChange}
                  disabled={!canManageGroup}
                  aria-label="公开群"
                />
              </div>
              {!canManageGroup ? (
                <p className="m-0 mt-2 text-xs text-muted-foreground">{canManage.disabledReason}</p>
              ) : null}
            </Card>

            <Card className="rounded-lg bg-card p-3 shadow-sm">
              <p className="m-0 mb-2 text-sm font-semibold text-foreground">群成员管理</p>
              <MemberList
                participants={group.participants}
                participantCount={group.participantCount}
                activeIdentity={activeIdentity}
                canManage={canManageGroup}
                disabledReason={canManage.disabledReason}
                emptyText="暂无成员"
                addLabel="添加群成员"
                onAddMany={handleAddMany}
                onRemove={props.onRemoveMember}
              />
            </Card>

            <Card className="rounded-lg bg-card p-3 shadow-sm">
              <p className="m-0 mb-2 text-sm font-semibold text-foreground">操作</p>
              <div className="flex flex-col gap-2">
                <Button
                  variant="ghost"
                  onClick={() => void handleShare()}
                  className="h-auto w-full justify-start rounded-lg border border-primary/25 bg-background px-3 py-2 text-left text-sm text-primary hover:bg-primary/10 hover:text-primary"
                >
                  <LinkIcon className="h-4 w-4 shrink-0" />
                  <span className="flex flex-col items-start">
                    <span>分享协作群</span>
                    <span className="text-[11px] font-normal text-muted-foreground">用户可以通过链接加入群组</span>
                  </span>
                </Button>
                {canManageGroup ? (
                  <ConfirmDialog
                    title="删除协作群"
                    description="删除后群内所有会话和数据将永久删除，且无法恢复。"
                    confirmText="确认删除"
                    confirmVariant="destructive"
                    onConfirm={onDissolve}
                  >
                    <Button
                      variant="ghost"
                      className="h-auto w-full justify-start rounded-lg border border-destructive/30 bg-background px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10"
                    >
                      <Trash2 className="h-4 w-4 shrink-0" />
                      <span className="flex flex-col items-start">
                        <span>删除协作群</span>
                        <span className="text-[11px] font-normal text-muted-foreground">
                          此操作不可恢复，请谨慎操作
                        </span>
                      </span>
                    </Button>
                  </ConfirmDialog>
                ) : isDirectMember ? (
                  <ConfirmDialog
                    title="退出协作群"
                    description="退出后将不再接收该群消息。"
                    confirmText="确认退出"
                    confirmVariant="destructive"
                    disabled={!activeIdentity}
                    onConfirm={() => {
                      if (activeIdentity) void onLeaveGroup(activeIdentity.id);
                      else toast.error('未选择当前身份');
                    }}
                  >
                    <Button
                      variant="ghost"
                      disabled={!activeIdentity}
                      className="h-auto w-full justify-start rounded-lg border border-destructive/30 bg-background px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10"
                    >
                      <LogOut className="h-4 w-4 shrink-0" />
                      <span className="flex flex-col items-start">
                        <span>退出协作群</span>
                        <span className="text-[11px] font-normal text-muted-foreground">退出后将不再接收该群消息</span>
                      </span>
                    </Button>
                  </ConfirmDialog>
                ) : null}
              </div>
            </Card>
          </div>
        ) : (
          <DingTalkConfigPanel
            canManage={canManageGroup}
            binding={props.dingTalkBinding}
            loading={props.dingTalkLoading}
            onSave={props.onSaveDingTalk}
            onToggleActive={props.onToggleDingTalkActive}
            onDelete={props.onDeleteDingTalk}
          />
        )}
      </div>

      <ShareDialog
        open={shareOpen}
        title="协作群"
        inviting={sharing}
        invitationUrl={shareUrl}
        onClose={() => setShareOpen(false)}
      />
    </aside>
  );
}
