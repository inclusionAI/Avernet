import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { BotDomain } from '@/domain/botWorkshop';
import type { BotCollaborator, BotSpaceOption } from '@/services/botWorkshop/botManagementService';
import { Plus, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';

interface Props {
  mode?: 'space' | 'authorize' | 'request';
  bot?: BotDomain;
  spaces: BotSpaceOption[];
  loading: boolean;
  collaborators: BotCollaborator[];
  onClose: () => void;
  onChangeSpace: (id: number) => Promise<void>;
  onAddCollaborator: (userId: string, role: BotCollaborator['role']) => Promise<void>;
  onUpdateCollaborator: (id: number, role: BotCollaborator['role']) => Promise<void>;
  onRemoveCollaborator: (id: number) => Promise<void>;
  onRequestAccess: (reason: string) => Promise<void>;
}

export function BotAccessModal(props: Props) {
  const { mode, bot, spaces, loading, collaborators, onClose } = props;
  const [spaceId, setSpaceId] = useState('');
  const [userId, setUserId] = useState('');
  const [reason, setReason] = useState('');
  useEffect(() => {
    setSpaceId('');
    setUserId('');
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
        ) : mode === 'request' ? (
          <div className="space-y-2">
            <p className="text-sm text-[var(--color-muted)]">请说明申请原因，将生成待 Owner 审批的工单。</p>
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
              <Input value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="输入用户 ID" />
              <Button
                variant="secondary"
                leftIcon={<Plus className="size-4" />}
                disabled={!userId.trim()}
                onClick={() => void props.onAddCollaborator(userId.trim(), 'member').then(() => setUserId(''))}
              >
                添加
              </Button>
            </div>
            {collaborators.length ? (
              collaborators.map((item) => (
                <div key={item.id} className="flex items-center gap-3 rounded-lg border border-border p-3">
                  <div className="min-w-0 flex-1">
                    <p className="m-0 text-sm font-medium">{item.name}</p>
                    <p className="m-0 text-xs text-[var(--color-muted)]">{item.userId}</p>
                  </div>
                  <Select
                    value={item.role}
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
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`移除 ${item.name}`}
                    leftIcon={<Trash2 className="size-4" />}
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
            取消
          </Button>
          {mode !== 'authorize' ? (
            <Button
              disabled={loading || (mode === 'space' ? !spaceId : !reason.trim())}
              onClick={() =>
                void (mode === 'space' ? props.onChangeSpace(Number(spaceId)) : props.onRequestAccess(reason.trim()))
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
