import { Button, Empty, IconButton, Skeleton } from '@/components/ui';
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
      <div className="ml-12 mt-2 self-start lg:hidden">
        <IconButton label="打开会话列表" icon={<List className="h-4 w-4" />} onClick={onOpenSessionList} />
      </div>
      {model.status === 'loading' ? (
        <div role="status" aria-label="加载会话列表" className="space-y-4 p-6">
          <Skeleton.Block className="h-10 w-1/3" />
          <Skeleton.Block className="h-24 w-2/3" />
        </div>
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
