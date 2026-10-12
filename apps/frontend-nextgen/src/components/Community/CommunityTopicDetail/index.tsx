import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { CommunityReply, CommunityTopic } from '@/domain/community/types';
import { ArrowLeft } from 'lucide-react';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';

function formatDateTime(value: string) {
  if (!value) return '时间未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  return date.toLocaleString('zh-CN');
}

interface FloorProps {
  floor: number;
  author: CommunityTopic['author'];
  body: string;
  createdAt: string;
}

// 布局：头像+名字+楼层 tag 一行（垂直居中）；正文与时间缩进对齐到名字下方。floor 0 = 楼主，N>0 = N 楼。
function Floor({ floor, author, body, createdAt }: FloorProps) {
  return (
    <article className="flex flex-col gap-2 border-b border-border py-5 last:border-b-0">
      <div className="flex items-center gap-3">
        <CommunityAuthorAvatar author={author} size={36} />
        <span className="font-medium text-foreground">{author.displayName}</span>
        <Badge tone={floor === 0 ? 'primary' : 'neutral'}>{floor === 0 ? '楼主' : `${floor} 楼`}</Badge>
      </div>
      <div className="pl-12">
        <p className="whitespace-pre-wrap text-sm leading-7 text-foreground">{body}</p>
        <div className="mt-2 text-xs text-muted-foreground">发表于：{formatDateTime(createdAt)}</div>
      </div>
    </article>
  );
}

export interface CommunityTopicDetailProps {
  topic: CommunityTopic | null;
  replies: CommunityReply[];
  loading: boolean;
  error: string | null;
  closing: boolean;
  onBack: () => void;
  onCloseTopic: () => Promise<boolean>;
  // 回帖分页
  replyLoading: boolean;
  replyError: string | null;
  replyPage: number;
  replyPageCount: number;
  replyPageSize: number;
  onGoToReplyPage: (page: number) => void;
}

export function CommunityTopicDetail({
  topic,
  replies,
  loading,
  error,
  closing,
  onBack,
  onCloseTopic,
  replyLoading,
  replyError,
  replyPage,
  replyPageCount,
  replyPageSize,
  onGoToReplyPage,
}: CommunityTopicDetailProps) {
  const floorOf = (index: number) => (replyPage - 1) * replyPageSize + index + 1;

  return (
    <div className="flex h-full flex-col bg-background">
      {/* 顶栏 sticky：返回/标题/结帖 + 回帖分页器（分页器置顶，滚动时常驻；不用滚到底再翻页）。 */}
      <div className="sticky top-0 z-10 border-b border-border bg-background/95 backdrop-blur">
        <header className="mx-auto flex w-full max-w-[1600px] items-center gap-2 px-4 py-3 sm:px-6">
          <Button
            variant="ghost"
            size="icon"
            leftIcon={<ArrowLeft className="size-4" aria-hidden />}
            onClick={onBack}
            aria-label="返回社区列表"
          />
          <div className="min-w-0 flex-1">
            <h1 className="m-0 truncate text-base font-semibold text-foreground">{topic?.title || '主题详情'}</h1>
          </div>
          {topic?.status === 'closed' && <Badge tone="neutral">已结帖</Badge>}
          {topic?.canClose && (
            <ConfirmDialog
              title="确认结帖？"
              description="结帖后该主题将显示为已结束，本期暂不支持重新打开。"
              confirmText="确认结帖"
              loading={closing}
              onConfirm={async () => {
                await onCloseTopic();
              }}
            >
              <Button variant="outline" size="sm">
                结帖
              </Button>
            </ConfirmDialog>
          )}
        </header>
        {replyPageCount > 1 && (
          <nav className="border-t border-border bg-background/80" aria-label="回帖分页">
            <div className="mx-auto flex w-full max-w-[1600px] items-center justify-end gap-3 px-4 py-2 sm:px-6 text-sm">
              <Button
                variant="outline"
                size="sm"
                disabled={replyPage <= 1 || replyLoading}
                onClick={() => onGoToReplyPage(replyPage - 1)}
              >
                上一页
              </Button>
              <span className="text-muted-foreground">
                第 {replyPage} / {replyPageCount} 页
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={replyPage >= replyPageCount || replyLoading}
                onClick={() => onGoToReplyPage(replyPage + 1)}
              >
                下一页
              </Button>
            </div>
          </nav>
        )}
      </div>
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1600px] flex-col p-4 sm:p-6 lg:p-8">
          {loading ? (
            <div className="space-y-3 py-4">
              <Skeleton.ListItem />
              <Skeleton.ListItem />
              <Skeleton.ListItem />
            </div>
          ) : error ? (
            <Empty
              title="详情加载失败"
              description={error}
              action={
                <Button variant="outline" onClick={onBack}>
                  返回列表
                </Button>
              }
            />
          ) : topic ? (
            <>
              {/* 楼主楼层仅在第 1 页显示：翻页后逐页是回帖流，不应每页都重复楼主正文。 */}
              {replyPage === 1 && (
                <Floor floor={0} author={topic.author} body={topic.body} createdAt={topic.createdAt} />
              )}
              {replies.map((reply, index) => (
                <Floor
                  key={reply.id}
                  floor={floorOf(index)}
                  author={reply.author}
                  body={reply.body}
                  createdAt={reply.createdAt}
                />
              ))}
              {!replies.length && <p className="py-6 text-center text-sm text-muted-foreground">暂时还没有回复</p>}
              {replyLoading && (
                <p aria-live="polite" className="py-3 text-center text-xs text-muted-foreground">
                  回复加载中...
                </p>
              )}
              {replyError && (
                <div className="mt-4 flex items-center justify-center gap-3 py-2">
                  <p className="m-0 text-sm text-muted-foreground">{replyError}</p>
                  <Button variant="secondary" size="sm" onClick={() => onGoToReplyPage(replyPage)}>
                    重试
                  </Button>
                </div>
              )}
            </>
          ) : null}
        </div>
      </main>
    </div>
  );
}
