import { Button } from '@/components';
import { cn } from '@/utils/utils';
import React from 'react';
import type { SpeakingLease } from '../utils/speakingLease';

export type SpeakingLeaseStatus = 'unheld' | 'held' | 'occupied' | 'error';

interface SpeakingLeaseControlProps {
  status: SpeakingLeaseStatus;
  lease?: SpeakingLease;
  nowMs?: number;
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

const SpeakingLeaseControl: React.FC<SpeakingLeaseControlProps> = ({
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
        status === 'held'
          ? 'border-green-200 bg-green-50 text-green-700'
          : status === 'occupied'
          ? 'border-amber-200 bg-amber-50 text-amber-700'
          : status === 'error'
          ? 'border-red-200 bg-red-50 text-red-600'
          : 'border-slate-200 bg-slate-50 text-slate-600',
      )}
      data-testid="speaking-lease-control"
    >
      <div className="min-w-0">
        <span className="font-medium">
          {status === 'held' ? '发言权已获取' : statusText[status]}
        </span>
        {status === 'held' && (
          <span className="ml-2 text-green-600">
            剩余 {remainingSeconds} 秒
          </span>
        )}
      </div>
      <Button
        size="xs"
        variant={status === 'occupied' ? 'danger' : 'secondary'}
        soft
        loading={isRequesting}
        onClick={action.onClick}
        disabled={status === 'held' && !lease}
      >
        {action.label}
      </Button>
    </div>
  );
};

export default SpeakingLeaseControl;
