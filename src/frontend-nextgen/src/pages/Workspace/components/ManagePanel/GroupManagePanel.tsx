import { Badge, Button, Card, Input, Segmented, Switch } from '@/components/ui';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { DeliveryPolicy, GroupView, IdentityView } from '@/domain/collaboration';
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
    deliveryPolicy?: DeliveryPolicy;
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
}

const KIND_LABEL: Record<GroupView['kind'], string> = {
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协同',
};

const DELIVERY_OPTIONS = [
  { value: 'send_to_driver' as const, label: '自动回复' },
  { value: 'inject_observers' as const, label: '关闭自动回复' },
];

export function GroupManagePanel(props: GroupManagePanelProps) {
  const { group, canManage, activeIdentity, onClose, onUpdate, onDissolve, onLeaveGroup } = props;
  const [tab, setTab] = useState<'basic' | 'advanced'>('basic');
  const [name, setName] = useState(group.name);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);

  const canManageGroup = canManage.allowed;

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

  const handleDeliveryChange = (value: DeliveryPolicy) => {
    if (!canManageGroup) return;
    void onUpdate({ deliveryPolicy: value });
  };

  return (
    <aside className="flex h-full flex-col bg-white">
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
          { value: 'advanced', label: '高级配置' },
        ]}
        onChange={setTab}
      />

      <div className="app-scrollbar flex-1 overflow-y-auto p-5">
        {tab === 'basic' ? (
          <div className="space-y-4">
            <Card className="rounded-2xl bg-white p-4 shadow-sm">
              <div className="mb-4 flex items-center justify-between gap-2">
                <Badge tone="primary">{KIND_LABEL[group.kind]}</Badge>
                <Badge tone={group.isPublic ? 'success' : 'neutral'}>{group.isPublic ? '公开群' : '私密群'}</Badge>
              </div>
              <label className="block">
                <span className="mb-1.5 block text-xs font-medium text-[var(--color-muted)]">群名称</span>
                <div className="flex gap-2">
                  <Input value={name} onChange={(event) => setName(event.target.value)} />
                  <Button variant="secondary" size="sm" onClick={handleSaveName}>
                    保存
                  </Button>
                </div>
              </label>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <div className="rounded-xl bg-[var(--color-panel-strong)] px-3 py-2.5">
                  <p className="m-0 text-[11px] font-medium text-[var(--color-muted)]">成员数量</p>
                  <p className="m-0 mt-1 text-sm font-medium text-[var(--color-fg)]">
                    {group.participantCount || group.participants.length}
                  </p>
                </div>
                <div className="rounded-xl bg-[var(--color-panel-strong)] px-3 py-2.5">
                  <p className="m-0 text-[11px] font-medium text-[var(--color-muted)]">创建时间</p>
                  <p className="m-0 mt-1 text-sm font-medium text-[var(--color-fg)]">
                    {new Date(group.createdAt).toLocaleDateString()}
                  </p>
                </div>
              </div>
            </Card>

            <Card className="rounded-2xl bg-white p-4 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="m-0 text-sm font-medium text-[var(--color-fg)]">公开群</p>
                  <p className="m-0 mt-0.5 text-xs text-[var(--color-muted)]">公开群允许通过邀请链接加入。</p>
                </div>
                <Switch
                  checked={group.isPublic}
                  onCheckedChange={handleVisibilityChange}
                  disabled={!canManageGroup}
                  aria-label="公开群"
                />
              </div>
              {!canManageGroup ? (
                <p className="m-0 mt-2 text-xs text-[var(--color-muted)]">{canManage.disabledReason}</p>
              ) : null}
            </Card>

            {group.kind === 'free_chat' && (
              <Card className="rounded-2xl bg-white p-4 shadow-sm">
                <p className="m-0 text-sm font-medium text-[var(--color-fg)]">投递策略</p>
                <p className="m-0 mt-1 text-xs text-[var(--color-muted)]">
                  自动回复：消息投递给 driver；关闭自动回复：仅注入观察者。
                </p>
                <div className="mt-3">
                  <Segmented<DeliveryPolicy>
                    value={group.deliveryPolicy ?? 'send_to_driver'}
                    options={DELIVERY_OPTIONS.map((o) => ({
                      ...o,
                      disabledReason: canManageGroup ? undefined : canManage.disabledReason ?? '无权限',
                    }))}
                    onChange={handleDeliveryChange}
                  />
                </div>
              </Card>
            )}

            <Card className="rounded-2xl bg-white p-4 shadow-sm">
              <p className="m-0 mb-3 text-sm font-semibold text-[var(--color-fg)]">群成员管理</p>
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
                    <span>分享协作群</span>
                    <span className="text-[11px] font-normal text-[var(--color-muted)]">用户可以通过链接加入群组</span>
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
                      className="h-auto w-full justify-start rounded-xl border border-[var(--color-error-soft)] bg-white px-4 py-2.5 text-left text-sm text-[var(--color-error)] hover:bg-[var(--color-error-soft)]/60"
                    >
                      <Trash2 className="h-4 w-4 shrink-0" />
                      <span className="flex flex-col items-start">
                        <span>删除协作群</span>
                        <span className="text-[11px] font-normal text-[var(--color-muted)]">
                          此操作不可恢复，请谨慎操作
                        </span>
                      </span>
                    </Button>
                  </ConfirmDialog>
                ) : (
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
                      className="h-auto w-full justify-start rounded-xl border border-[var(--color-error-soft)] bg-white px-4 py-2.5 text-left text-sm text-[var(--color-error)] hover:bg-[var(--color-error-soft)]/60"
                    >
                      <LogOut className="h-4 w-4 shrink-0" />
                      <span className="flex flex-col items-start">
                        <span>退出协作群</span>
                        <span className="text-[11px] font-normal text-[var(--color-muted)]">
                          退出后将不再接收该群消息
                        </span>
                      </span>
                    </Button>
                  </ConfirmDialog>
                )}
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
