import { Empty, IconButton } from '@/components/ui';
import { PrimaryNavToggle } from '@/components/Workspace/PrimaryNav';
import { ChevronRight, Sparkles } from 'lucide-react';

export function GroupChatPlaceholder({ onOpenSessionList }: { onOpenSessionList?: () => void }) {
  return (
    <section className="flex min-w-0 flex-1 flex-col bg-background">
      {/* 未选群也保留 <lg 双入口，否则内流侧栏隐藏后无法切换身份或选择群。 */}
      {onOpenSessionList ? (
        <div className="flex h-12 shrink-0 items-center gap-2 border-b border-divider bg-card px-3 sm:px-4 lg:hidden">
          <PrimaryNavToggle />
          <IconButton
            label="打开会话列表"
            icon={<ChevronRight className="h-5 w-5" aria-hidden />}
            size="sm"
            onClick={onOpenSessionList}
          />
        </div>
      ) : null}
      <div className="flex flex-1 items-center justify-center">
        <Empty
          title="欢迎进入协作群对话现场"
          description="在左侧选择一个协作群，开始与多个 Bot 和用户协同。"
          icon={<Sparkles className="h-5 w-5" />}
        />
      </div>
    </section>
  );
}
