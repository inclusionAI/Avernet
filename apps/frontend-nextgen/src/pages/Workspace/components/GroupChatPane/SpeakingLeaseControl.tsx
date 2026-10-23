/**
 * 发言权条——lease_required 会话在输入区上方渲染的租约状态与操作条。
 *
 * 四态展示 + 剩余秒倒计时 + 获取/抢占/释放操作；持有者判定与续期均由 BCS
 * 服务端权威裁决（认证连接解析 holderActorId），本组件只回显快照结果。
 * 状态配色走语义 token（success / warning / destructive / muted）。
 */
import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import type { GroupSpeakingLeaseModel } from '@/pages/Workspace/hooks/useSpeakingLease';
import React from 'react';
import type { SpeakingLease, SpeakingLeaseStatus } from '@/domain/collaboration/speakingLease';

export type { SpeakingLeaseStatus } from '@/domain/collaboration/speakingLease';

export interface SpeakingLeaseControlProps {
  status: SpeakingLeaseStatus;
  lease?: SpeakingLease;
  /** held 状态下的 1s 步进时钟（毫秒），驱动剩余秒倒计时。 */
  nowMs?: number;
  /** 有指令在途时按钮 loading。 */
  isRequesting?: boolean;
  onAcquire: () => void;
  onPreempt: () => void;
  onRelease: () => void;
}

const statusText: Record<SpeakingLeaseStatus, string> = {
  unheld: '需要获取发言权后才能发送 Caller 群消息',
  held: '发言权已获取',
  occupied: '发言权被占用',
  error: '发言权状态未知',
};

const statusClasses: Record<SpeakingLeaseStatus, string> = {
  held: 'border-success/30 bg-success/10 text-success',
  occupied: 'border-warning/30 bg-warning/10 text-warning',
  error: 'border-destructive/30 bg-destructive/10 text-destructive',
  unheld: 'border-input bg-muted text-muted-foreground',
};

export const SpeakingLeaseControl: React.FC<SpeakingLeaseControlProps> = ({
  status,
  lease,
  nowMs,
  isRequesting,
  onAcquire,
  onPreempt,
  onRelease,
}) => {
  const remainingSeconds =
    status === 'held' && lease
      ? Math.max(
          0,
          Math.ceil((lease.expiresAtMs - (nowMs ?? Date.now()) - 1) / 1000),
        )
      : undefined;
  const action =
    status === 'held'
      ? { label: '释放', onClick: onRelease }
      : status === 'occupied'
      ? { label: '抢占发言权', onClick: onPreempt, danger: true }
      : {
          label: status === 'error' ? '重试' : '获取发言权',
          onClick: onAcquire,
        };

  return (
    <div
      className={cn(
        'flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-xs',
        statusClasses[status],
      )}
      data-testid="speaking-lease-control"
    >
      <div className="min-w-0">
        <span className="font-medium">
          {status === 'held' ? '发言权已获取' : statusText[status]}
        </span>
        {status === 'held' && (
          <span className="ml-2 text-success">剩余 {remainingSeconds} 秒</span>
        )}
      </div>
      <Button
        size="sm"
        variant={action.danger ? 'destructive' : 'secondary'}
        loading={isRequesting}
        onClick={action.onClick}
        disabled={status === 'held' && !lease}
      >
        {action.label}
      </Button>
    </div>
  );
};

/**
 * 群聊输入区上方的发言权条装配（模型直通）。
 * 非 lease_required 会话（或模型缺失）渲染 null——普通会话零感知。
 */
export function SpeakingLeaseStrip({ model }: { model?: GroupSpeakingLeaseModel }) {
  if (!model || model.speakingMode !== 'lease_required') return null;
  return (
    <SpeakingLeaseControl
      status={model.speakingLeaseStatus}
      lease={model.speakingLease}
      nowMs={model.leaseNowMs}
      isRequesting={model.isUpdatingSpeakingLease}
      onAcquire={() => void model.acquireSpeakingLease()}
      onPreempt={() => void model.acquireSpeakingLease({ preempt: true })}
      onRelease={() =>
        void (model.speakingLease && model.releaseSpeakingLease(model.speakingLease))
      }
    />
  );
}
