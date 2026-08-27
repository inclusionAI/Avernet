import { Badge, Button, Card, Segmented } from '@/components/ui';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/AlertDialog';
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/components/ui/Drawer';
import type { DeliveryPolicy, GroupView } from '@/domain/collaboration';
import type { PolicyResult } from '@/services/workspace/groupService';
import type { DomainResult } from '@/services/workspace/identityService';
import { useState } from 'react';

export interface GroupManageDrawerProps {
  group: GroupView | null;
  canManage: PolicyResult;
  onClose: () => void;
  onUpdate: (patch: {
    name?: string;
    visibility?: 'private' | 'public';
    deliveryPolicy?: DeliveryPolicy;
  }) => Promise<DomainResult<GroupView>> | void;
  onDissolve: () => void;
}

const VISIBILITY_OPTIONS = [
  { value: 'private' as const, label: '私密' },
  { value: 'public' as const, label: '公开' },
];

const DELIVERY_OPTIONS = [
  { value: 'send_to_driver' as const, label: '自动回复' },
  { value: 'inject_observers' as const, label: '关闭自动回复' },
];

const DELIVERY_LABEL: Record<DeliveryPolicy, string> = {
  send_to_driver: '自动回复',
  inject_observers: '关闭自动回复',
};

/**
 * 群管理抽屉：基于 sprint Drawer（右侧 size='md'）+ 内嵌 AlertDialog 解散确认。
 * Prop-driven：onUpdate/onDissolve 由父层传入，不直接依赖 groupService。
 */
export function GroupManageDrawer({ group, canManage, onClose, onUpdate, onDissolve }: GroupManageDrawerProps) {
  const [confirmOpen, setConfirmOpen] = useState(false);

  const disabled = !canManage.allowed;
  const disabledReason = canManage.disabledReason;

  const visibilityValue: 'private' | 'public' = group ? (group.isPublic ? 'public' : 'private') : 'private';
  const deliveryValue: DeliveryPolicy = group?.deliveryPolicy ?? 'send_to_driver';

  const visOptions = disabled
    ? VISIBILITY_OPTIONS.map((o) => ({ ...o, disabledReason: disabledReason ?? '无权限' }))
    : VISIBILITY_OPTIONS;
  const deliveryOptions = disabled
    ? DELIVERY_OPTIONS.map((o) => ({ ...o, disabledReason: disabledReason ?? '无权限' }))
    : DELIVERY_OPTIONS;

  const handleVisibilityChange = (next: 'private' | 'public') => {
    if (disabled || !group) return;
    void onUpdate({ visibility: next });
  };

  const handleDeliveryChange = (next: DeliveryPolicy) => {
    if (disabled || !group) return;
    void onUpdate({ deliveryPolicy: next });
  };

  return (
    <Drawer open={!!group} onOpenChange={(open) => !open && onClose()}>
      <DrawerContent side="right" size="md" closeLabel="关闭群管理抽屉">
        <DrawerHeader>
          <DrawerTitle className="flex items-center gap-2 text-base font-semibold text-[var(--color-fg)]">
            群管理
            {group && <Badge tone="neutral">{group.name}</Badge>}
          </DrawerTitle>
        </DrawerHeader>

        {/* 权限提示 */}
        {disabled && disabledReason && (
          <p className="mb-3 rounded-lg bg-[var(--color-warning-soft)] px-3 py-2 text-sm text-[var(--color-warning)]">
            {disabledReason}
          </p>
        )}

        {/* 公开性 */}
        <Card className="p-4">
          <p className="m-0 text-sm font-medium text-[var(--color-fg)]">公开性</p>
          <p className="mt-1 text-xs text-[var(--color-muted)]">私密群仅成员可见；公开群允许通过链接加入。</p>
          <div className="mt-3">
            <Segmented<'private' | 'public'>
              value={visibilityValue}
              options={visOptions}
              onChange={handleVisibilityChange}
            />
          </div>
        </Card>

        {/* 投递策略 */}
        <Card className="mt-3 p-4">
          <div className="flex items-center justify-between">
            <p className="m-0 text-sm font-medium text-[var(--color-fg)]">投递策略</p>
            {group && group.kind !== 'free_chat' && <Badge tone="neutral">仅自由聊天可配置</Badge>}
          </div>
          <p className="mt-1 text-xs text-[var(--color-muted)]">
            自动回复：消息投递给 driver 触发回复；关闭自动回复：仅注入观察者不触发回复。
          </p>
          <div className="mt-3">
            {group?.kind === 'free_chat' ? (
              <Segmented<DeliveryPolicy>
                value={deliveryValue}
                options={deliveryOptions}
                onChange={handleDeliveryChange}
              />
            ) : (
              <p className="m-0 rounded-lg bg-[var(--color-panel-strong)] px-3 py-2 text-sm text-[var(--color-muted)]">
                当前：{DELIVERY_LABEL[deliveryValue]}（只读）
              </p>
            )}
          </div>
        </Card>

        {/* 危险操作 */}
        <Card className="mt-3 border-[var(--color-error-soft)] p-4">
          <p className="m-0 text-sm font-medium text-[var(--color-error)]">解散群</p>
          <p className="mt-1 text-xs text-[var(--color-muted)]">该操作不可撤销，所有会话历史将一并清除。</p>
          <div className="mt-3">
            <Button variant="destructive" size="md" disabled={disabled} onClick={() => setConfirmOpen(true)}>
              解散群
            </Button>
          </div>
        </Card>

        <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>解散群</AlertDialogTitle>
              <AlertDialogDescription>
                确定要解散该协作群吗？解散后将无法恢复，且所有会话历史将被移除
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                onClick={() => {
                  setConfirmOpen(false);
                  onDissolve();
                }}
              >
                确认解散
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DrawerContent>
    </Drawer>
  );
}

export default GroupManageDrawer;
