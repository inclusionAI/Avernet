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
export function GroupAvatarStack({ participants, totalCount }: { participants: ParticipantView[]; totalCount: number }) {
  const shown = participants.slice(0, AVATAR_STACK_MAX);
  const remaining = Math.max(0, totalCount - shown.length);
  if (shown.length === 0) return null;
  return (
    <span className="-mr-1 flex h-4 shrink-0 items-center" aria-label={`成员 ${totalCount} 人`}>
      {shown.map((participant, index) => (
        <span
          key={participant.actorId ?? participant.name}
          className={cn('rounded-full ring-1 ring-background', index > 0 && '-ml-[7px]')}
        >
          <Avatar
            name={participant.name}
            src={participant.avatarUrl && isHttpUrl(participant.avatarUrl) ? participant.avatarUrl : undefined}
            size={16}
          />
        </span>
      ))}
      {remaining > 0 && (
        <span
          aria-hidden="true"
          className="-ml-[7px] flex h-4 min-w-4 items-center justify-center rounded-full bg-secondary px-0.5 text-[8px] leading-none text-secondary-foreground ring-1 ring-background"
        >
          {remaining}+
        </span>
      )}
    </span>
  );
}