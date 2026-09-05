import { SessionTabs, type SessionTabValue } from './SessionTabs';

interface SessionToolbarProps {
  value: SessionTabValue;
  onChange: (value: SessionTabValue) => void;
  allCount?: number;
  favoriteCount?: number;
  favoriteDisabledReason?: string;
  className?: string;
}

/** 群/Bot 共用的会话范围工具栏：仅承载会话范围筛选与统计。 */
export function SessionToolbar({
  value,
  onChange,
  allCount,
  favoriteCount,
  favoriteDisabledReason,
  className,
}: SessionToolbarProps) {
  return (
    <div
      role="group"
      aria-label="会话范围筛选"
      className={`flex min-h-9 items-center border-b border-border/60 bg-muted/30 px-3 pr-[18px] ${className ?? ''}`}
    >
      <SessionTabs
        value={value}
        onChange={onChange}
        allCount={allCount}
        favoriteCount={favoriteCount}
        showCount
        favoriteDisabledReason={favoriteDisabledReason}
      />
    </div>
  );
}
