import { Button, Empty, Skeleton } from '@/components/ui';

/** 受限资源被删除/退出后的稳定落点，不回落到另一群或会话。 */
export function ScopedWorkspaceUnavailable({
  error,
  loading,
  onRetry,
}: {
  error: string | null;
  loading: boolean;
  onRetry: () => void;
}) {
  return (
    <div className="flex min-w-0 flex-1 items-center justify-center">
      {loading ? (
        <Skeleton.Block aria-label="重新加载指定协作区" className="h-24 w-2/3" />
      ) : (
        <Empty
          title="指定协作区已不可用"
          description={error ?? '群或会话已退出、删除或不可访问，请返回完整协作区。'}
          action={
            error ? (
              <Button variant="outline" size="sm" onClick={onRetry}>
                重试
              </Button>
            ) : undefined
          }
        />
      )}
    </div>
  );
}
