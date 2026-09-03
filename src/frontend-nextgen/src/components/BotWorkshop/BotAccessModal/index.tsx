import type { SearchedUser } from '@/capabilities';
import { UserSearchDropdown } from '@/components/Admin/SpaceMemberList/UserSearchDropdown';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { BotDomain } from '@/domain/botWorkshop';
import type { BotCollaborator, BotSpaceOption } from '@/services/botWorkshop/botManagementService';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';

interface Props {
  mode?: 'space' | 'authorize' | 'request';
  bot?: BotDomain;
  spaces: BotSpaceOption[];
  loading: boolean;
  operation?: string;
  collaborators: BotCollaborator[];
  onClose: () => void;
  onChangeSpace: (id: number) => Promise<void>;
  onCreateTeamAndChangeSpace: (name: string) => Promise<void>;
  onAddCollaborator: (userId: string, name: string | undefined, role: BotCollaborator['role']) => Promise<boolean>;
  onUpdateCollaborator: (id: number, role: BotCollaborator['role']) => Promise<void>;
  onRemoveCollaborator: (id: number) => Promise<void>;
  onRequestAccess: (reason: string) => Promise<void>;
}

export function BotAccessModal(props: Props) {
  const { mode, bot, spaces, loading, operation, collaborators, onClose } = props;
  const [spaceId, setSpaceId] = useState('');
  const [selectedUser, setSelectedUser] = useState<SearchedUser>();
  const [spaceMode, setSpaceMode] = useState<'existing' | 'create'>('existing');
  const [teamName, setTeamName] = useState('');
  const [reason, setReason] = useState('');
  useEffect(() => {
    setSpaceId('');
    setSelectedUser(undefined);
    setSpaceMode('existing');
    setTeamName('');
    setReason('');
  }, [bot?.id, mode]);
  if (!mode || !bot) return null;
  const title = mode === 'space' ? '变更归属空间' : mode === 'authorize' ? '授权协作' : '申请操作权限';
  return (
    <Modal open onOpenChange={(open) => !open && onClose()}>
      <ModalContent size="lg">
        <ModalHeader>
          <ModalTitle>
            {title} · {bot.name}
          </ModalTitle>
        </ModalHeader>
        {mode === 'space' ? (
          <div className="space-y-4">
            <div className="flex rounded-lg bg-muted p-1">
              <Button
                variant={spaceMode === 'existing' ? 'secondary' : 'ghost'}
                size="sm"
                className="flex-1"
                onClick={() => setSpaceMode('existing')}
              >
                迁移到已有团队
              </Button>
              <Button
                variant={spaceMode === 'create' ? 'secondary' : 'ghost'}
                size="sm"
                className="flex-1"
                onClick={() => setSpaceMode('create')}
              >
                创建新团队
              </Button>
            </div>
            {spaceMode === 'existing' ? (
              <Select value={spaceId} onValueChange={setSpaceId}>
                <SelectTrigger aria-label="目标空间">
                  <SelectValue placeholder="请选择目标空间" />
                </SelectTrigger>
                <SelectContent>
                  {spaces.map((space) => (
                    <SelectItem key={space.id} value={String(space.id)}>
                      {space.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <div className="space-y-2">
                <Input
                  value={teamName}
                  onChange={(event) => setTeamName(event.target.value)}
                  placeholder="新团队名称"
                />
                <p className="m-0 text-xs text-muted-foreground">将创建团队空间，并在创建成功后把 Bot 迁入该团队。</p>
              </div>
            )}
          </div>
        ) : mode === 'request' ? (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">请说明申请原因，将生成待 Owner 审批的工单。</p>
            <Input
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="申请原因"
              maxLength={512}
            />
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <UserSearchDropdown
                className="min-w-0 flex-1"
                disabled={operation === 'add'}
                disabledUserIds={collaborators.map((item) => item.userId)}
                onSelect={setSelectedUser}
              />
              <Button
                variant="secondary"
                leftIcon={<Plus className="size-4" />}
                loading={operation === 'add'}
                disabled={!selectedUser}
                onClick={() =>
                  selectedUser &&
                  void props
                    .onAddCollaborator(
                      selectedUser.userId,
                      selectedUser.nickName || selectedUser.realName || selectedUser.displayName,
                      'member',
                    )
                    .then((added) => added && setSelectedUser(undefined))
                }
              >
                添加
              </Button>
            </div>
            {selectedUser ? (
              <p className="m-0 text-xs text-muted-foreground">
                待添加：
                {selectedUser.nickName || selectedUser.realName || selectedUser.displayName || selectedUser.userId}（
                {selectedUser.userId}）
              </p>
            ) : null}
            {collaborators.length ? (
              collaborators.map((item) => (
                <div key={item.id} className="flex items-center gap-3 rounded-lg border border-border p-3">
                  <div className="min-w-0 flex-1">
                    <p className="m-0 text-sm font-medium">{item.name}</p>
                    <p className="m-0 text-xs text-muted-foreground">{item.userId}</p>
                  </div>
                  <Select
                    value={item.role}
                    disabled={operation === `update:${item.id}`}
                    onValueChange={(role) => void props.onUpdateCollaborator(item.id, role as BotCollaborator['role'])}
                  >
                    <SelectTrigger className="w-28" aria-label={`${item.name} 权限`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="member">可编辑</SelectItem>
                      <SelectItem value="admin">管理员</SelectItem>
                    </SelectContent>
                  </Select>
                  {operation === `update:${item.id}` ? (
                    <Loader2 aria-label="角色更新中" className="size-4 animate-spin text-primary" />
                  ) : null}
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`移除 ${item.name}`}
                    leftIcon={<Trash2 className="size-4" />}
                    loading={operation === `remove:${item.id}`}
                    disabled={Boolean(operation)}
                    onClick={() => void props.onRemoveCollaborator(item.id)}
                  />
                </div>
              ))
            ) : (
              <Empty compact title="暂无协作者" description="输入用户 ID 授予 Bot 操作权限。" />
            )}
          </div>
        )}
        <ModalFooter>
          <Button variant="secondary" onClick={onClose}>
            {mode === 'authorize' ? '完成' : '取消'}
          </Button>
          {mode !== 'authorize' ? (
            <Button
              disabled={
                loading ||
                (mode === 'space' ? (spaceMode === 'existing' ? !spaceId : !teamName.trim()) : !reason.trim())
              }
              onClick={() =>
                void (mode === 'space'
                  ? spaceMode === 'existing'
                    ? props.onChangeSpace(Number(spaceId))
                    : props.onCreateTeamAndChangeSpace(teamName.trim())
                  : props.onRequestAccess(reason.trim()))
              }
            >
              {loading ? '处理中…' : '确认'}
            </Button>
          ) : null}
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
