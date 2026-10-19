import { ChatMessageSkeleton } from '@/components/Workspace/ChatPanel/ChatMessageSkeleton';
import { PrimaryNavToggle } from '@/components/Workspace/PrimaryNav';
import { Button, Empty, IconButton } from '@/components/ui';
import { List, MessageSquare, Plus } from 'lucide-react';
import type { ConversationSessionPlaceholderModel } from '../hooks/useConversationSessionPlaceholder';

export function ConversationSessionPlaceholder({
  model,
  onOpenSessionList,
}: {
  model: ConversationSessionPlaceholderModel;
  onOpenSessionList(): void;
}) {
  return (
    <section aria-label={`${model.botName} 的会话`} className="flex min-w-0 flex-1 flex-col bg-background">
      {/* 占位顶行（加载/失败/空态也保留 <lg 顶行双入口）：壳内一级导航入口与页头同构，
          置于「打开会话列表」左侧——无页头阶段不回退到壳的独立条带，避免阶段切换条带闪跳；
          壳外/精简路由下导航入口自动为 null，仅剩会话列表入口。 */}
      <div className="ml-12 mt-2 flex items-center gap-1 self-start lg:hidden">
        <PrimaryNavToggle />
        <IconButton label="打开会话列表" icon={<List className="h-4 w-4" />} onClick={onOpenSessionList} />
      </div>
      {model.status === 'loading' ? (
        // 展开 Bot 后会话列表在途：借用 ChatMessageSkeleton 的「誊写稿」占位——
        // 主导走向是自动选中会话后 ChatPanel 仍以同一骨架装载消息，全程同一画布语言
        // （expand-selection spec 的零跳变延伸）；本段仅短暂在途，读屏话术覆写为「加载会话列表」。
        <ChatMessageSkeleton ariaLabel="加载会话列表" />
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <Empty
            icon={<MessageSquare className="h-5 w-5" />}
            title={
              model.status === 'error'
                ? '加载会话失败'
                : model.status === 'empty'
                ? `${model.botName} 暂无会话`
                : model.status === 'favorite-empty'
                ? '暂无已收藏会话'
                : '请选择一个会话'
            }
            description={
              model.status === 'error'
                ? model.error
                : model.status === 'empty'
                ? '创建会话后，即可开始与此 Bot 对话。'
                : model.status === 'favorite-empty'
                ? '切换到全部会话查看已有会话，或创建新会话。'
                : '请从左侧列表选择会话；如尚未加载目标会话，可继续加载更多。'
            }
            action={
              model.status === 'empty' ? (
                <Button
                  disabled={model.creating}
                  leftIcon={<Plus className="h-4 w-4" />}
                  onClick={() => void model.create()}
                >
                  {model.creating ? '创建中…' : '创建会话'}
                </Button>
              ) : model.status === 'error' ? (
                <Button variant="outline" onClick={model.retry}>
                  重试
                </Button>
              ) : model.status === 'favorite-empty' ? (
                <Button variant="outline" onClick={model.showAll}>
                  查看全部会话
                </Button>
              ) : undefined
            }
          />
        </div>
      )}
    </section>
  );
}
