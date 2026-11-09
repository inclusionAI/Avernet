import { ChatMessageSkeleton } from '@/components/Workspace/ChatPanel/ChatMessageSkeleton';
import { Button, Empty } from '@/components/ui';
import type { ConversationDirectoryModel } from '../hooks/useConversationDirectory';

/** An unresolved route is pending only while its directory is actually loading. */
export function ConversationTargetPlaceholder({ directory }: { directory: ConversationDirectoryModel }) {
  const loading = directory.managedLoading || directory.teamLoading || directory.friendLoading;
  const error = directory.managedError || directory.teamError || directory.friendError;
  return (
    <section aria-label="定位 Bot 会话" className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
      {loading ? (
        <ChatMessageSkeleton ariaLabel="加载 Bot 会话" />
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <Empty
            title={error ? '加载 Bot 目录失败' : '无法定位此 Bot'}
            description={error || '该链接无法唯一匹配可访问的 Bot，请从左侧列表重新选择。'}
            action={error ? (
              <Button variant="outline" onClick={() => {
                directory.retryManaged(); // The managed reload also reloads the friend directory.
                directory.retryTeam();
              }}>
                重试
              </Button>
            ) : undefined}
          />
        </div>
      )}
    </section>
  );
}
