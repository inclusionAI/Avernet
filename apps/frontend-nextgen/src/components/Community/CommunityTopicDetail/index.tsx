import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { CommunityReply, CommunityTopic } from '@/domain/community/types';
import { ArrowLeft, User } from 'lucide-react';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';

function formatDateTime(value: string) {
  if (!value) return '时间未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '时间未知';
  const pad = (input: number) => String(input).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

interface FloorProps {
  floor: number;
  author: CommunityTopic['author'];
  body: string;
  createdAt: string;
}

// 布局：头像+名字+楼层 tag 一行（垂直居中）；正文与时间缩进对齐到名字下方。floor 0 = 楼主，N>0 = N楼。
function Floor({ floor, author, body, createdAt }: FloorProps) {
  return (
    <article className="border-b border-border/60 py-4 last:border-b-0">
      <div className="flex items-center gap-2">
        <CommunityAuthorAvatar author={author} size={36} />
        <span className="text-sm font-semibold text-foreground">{author.displayName}</span>
        <Badge
          tone={floor === 0 ? 'primary' : 'neutral'}
          className={
            floor === 0
              ? 'gap-1 rounded bg-foreground px-1.5 py-0.5 text-[10px] text-background'
              : 'h-6 rounded px-2 py-0 text-[10px]'
          }
        >
          {floor === 0 ? <User className="size-3" aria-hidden /> : null}
          {floor === 0 ? '楼主' : `${floor}楼`}
        </Badge>
      </div>
      <div className="mt-2 pl-[44px] pr-[44px]">
        <div className="min-h-[120px]">
          <p className="m-0 whitespace-pre-wrap text-sm leading-relaxed text-foreground">{body}</p>
        </div>
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
      {/* 原型结构：头部固定、主题流单独滚动。返回 / 标题 / 状态 / 分页收敛在同一行右列。 */}
      <header className="flex w-full items-start gap-3 px-4 pb-4 pt-6 sm:px-6 lg:px-10">
        <Button variant="ghost" size="icon" onClick={onBack} aria-label="返回社区列表" className="mt-1 shrink-0">
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <div className="min-w-0 flex-1">
          <h2 className="m-0 min-w-0 truncate text-lg font-semibold text-foreground">{topic?.title || '主题详情'}</h2>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            {topic?.status === 'closed' && <Badge tone="outline">已结帖</Badge>}
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
                <Button variant="outline" size="sm" className="h-8 px-3 text-xs">
                  结帖
                </Button>
              </ConfirmDialog>
            )}
          </div>
          {replyPageCount > 1 && (
            <div className="flex items-center gap-1 text-xs text-muted-foreground" aria-label="回帖分页">
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-2.5"
                disabled={replyPage <= 1 || replyLoading}
                onClick={() => onGoToReplyPage(replyPage - 1)}
              >
                上一页
              </Button>
              <span className="px-1 tabular-nums">
                {replyPage} / {replyPageCount}
              </span>
              <Button
                variant="outline"
                size="sm"
                className="h-7 px-2.5"
                disabled={replyPage >= replyPageCount || replyLoading}
                onClick={() => onGoToReplyPage(replyPage + 1)}
              >
                下一页
              </Button>
            </div>
          )}
        </div>
      </header>
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col px-4 pb-6 sm:px-6 lg:px-10">
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
