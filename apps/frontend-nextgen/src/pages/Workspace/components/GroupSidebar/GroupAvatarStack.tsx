import type { ParticipantView } from '@/domain/collaboration/types';
import { Avatar } from '@/components/ui';
import { cn } from '@/utils/cn';

/** 头像堆最多展示成员数（dmore artboard-010「运营协作」行：2 个 16px 真实头像 + N+ 徽标）。 */
const AVATAR_STACK_MAX = 2;

function isHttpUrl(url: string): boolean {
  return /^https?:\/\//.test(url);
}

/**
 * 成员头像堆（dmore artboard-010「群目录一级_收起/展开」两级行首 34x16 容器实测）：
 * ≤2 个 16px 真实头像按 9px 步距叠放,与标题净距 4px（行 gap-2 由堆上 -mr-1 抵 4px）;
 * 超出部分以 N+ 徽标（8px 字、E8EAED 底）表达剩余人数。participants 未回填时不渲染行首
 * 标识（2026-10-12 用户反馈：群行不用文件夹图标,列表缺成员时由 hook 层按详情补拉）。
 */
export function GroupAvatarStack({
  participants,
  totalCount,
  size = 16,
}: {
  participants: ParticipantView[];
  totalCount: number;
  /** 侧栏放大头像；聊天标题等已有调用保持紧凑尺寸。 */
  size?: 16 | 20;
}) {
  const shown = participants.slice(0, AVATAR_STACK_MAX);
  const remaining = Math.max(0, totalCount - shown.length);
  if (shown.length === 0) return null;
  return (
    <span className={cn('-mr-1 flex shrink-0 items-center', size === 20 ? 'h-5' : 'h-4')} aria-label={`成员 ${totalCount} 人`}>
      {shown.map((participant, index) => (
        <span
          key={participant.actorId ?? participant.name}
          className={cn('rounded-full ring-1 ring-background', index > 0 && (size === 20 ? '-ml-3' : '-ml-[7px]'))}
        >
          <Avatar
            name={participant.name}
            src={participant.avatarUrl && isHttpUrl(participant.avatarUrl) ? participant.avatarUrl : undefined}
            size={size}
          />
        </span>
      ))}
      {remaining > 0 && (
        <span
          aria-hidden="true"
          className={cn(
            'flex items-center justify-center rounded-full bg-secondary px-0.5 leading-none text-secondary-foreground ring-1 ring-background',
            size === 20 ? '-ml-3 h-5 min-w-5 text-[9px]' : '-ml-[7px] h-4 min-w-4 text-[8px]',
          )}
        >
          {remaining}+
        </span>
      )}
    </span>
  );
}
