import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import type { CommunityAttachment, CommunityReply, CommunityTopic } from '@/domain/community/types';
import { ArrowLeft, CircleHelp, Download, Paperclip } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Floor } from './Floor';
import { PollTypeIcon } from '../PollTypeIcon';
import { PollPanel } from './PollPanel';
import { buildReplyGroups } from './model';

export interface CommunityTopicDetailProps {
  topic: CommunityTopic | null;
  replies: CommunityReply[];
  loading: boolean;
  error: string | null;
  closing: boolean;
  onBack: () => void;
  onCloseTopic: () => Promise<boolean>;
  onDownloadAttachment: (attachment: CommunityAttachment) => void | Promise<void>;
  // 回帖分页
  replyLoading: boolean;
  replyError: string | null;
  replyPage: number;
  replyPageCount: number;
  replyPageSize: number;
  onGoToReplyPage: (page: number) => void;
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KiB`;
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MiB`;
}

export function CommunityTopicDetail({
  topic,
  replies,
  loading,
  error,
  closing,
  onBack,
  onCloseTopic,
  onDownloadAttachment,
  replyLoading,
  replyError,
  replyPage,
  replyPageCount,
  replyPageSize,
  onGoToReplyPage,
}: CommunityTopicDetailProps) {
  const [expandedReplyIds, setExpandedReplyIds] = useState<Record<string, boolean>>({});
  const replyElementsRef = useRef(new Map<string, HTMLElement | null>());

  const replyOffset = (replyPage - 1) * replyPageSize;
  const floorGroups = useMemo(
    () => buildReplyGroups(replies, replyOffset + 1),
    [replies, replyOffset],
  );
  const registerReplyElement = useCallback(
    (postId?: string) =>
      (element: HTMLElement | null) => {
        if (postId) replyElementsRef.current.set(postId, element);
      },
    [],
  );

  const locateTargetReply = useCallback((postId: string) => {
    const element = replyElementsRef.current.get(postId);
    if (!element) return;
    element.scrollIntoView({ block: 'center', behavior: 'smooth' });
    element.classList.add('bg-primary/10');
    window.setTimeout(() => element.classList.remove('bg-primary/10'), 1200);
  }, []);

  const toggleThreadReplies = useCallback((postId: string) => {
    setExpandedReplyIds((previous) => ({ ...previous, [postId]: !previous[postId] }));
  }, []);

  // 楼中楼是楼层页面内的局部展开状态；翻页后必须回到默认展示前 2 条。
  useEffect(() => {
    setExpandedReplyIds({});
  }, [replyPage]);

  return (
    <div className="flex h-full flex-col bg-background">
      {/* 原型结构：头部固定、主题流单独滚动。返回 / 标题 / 状态 / 分页收敛在同一行右列。 */}
      <header className="flex w-full items-start gap-3 px-4 pb-4 pt-6 sm:px-6 lg:px-10">
        <Button variant="ghost" size="icon" onClick={onBack} aria-label="返回社区列表" className="mt-1 shrink-0">
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <div className="min-w-0 flex-1">
          <h2 className="m-0 min-w-0 truncate text-lg font-semibold text-foreground">{topic?.title || '主题详情'}</h2>
          {!!topic?.analysis?.tags?.length && (
            <div className="mt-1 flex flex-wrap gap-1">
              {topic.analysis.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium text-primary"
                >
                  #{tag}
                </span>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            {topic?.topicType === 'poll' && <PollTypeIcon className="size-4" />}
            {topic?.topicType === 'share' && (
              <Paperclip className="size-4 text-muted-foreground" role="img" aria-label="分享附件" />
            )}
            {topic?.topicType === 'help' && <Badge tone="warning">求助</Badge>}
            {topic?.topicType === 'notice' && <Badge tone="warning">通知</Badge>}
            {topic?.status === 'closed' && topic.topicType === 'help' && (
              <Badge tone="success">已解决</Badge>
            )}
            {topic?.status === 'closed' && topic.topicType !== 'help' && (
              <Badge tone="outline">已结帖</Badge>
            )}
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
              {/* 楼主楼层仅在第 1 页显示：分页语义本身一直以一级楼层为单位。 */}
              {replyPage === 1 && (
                <Floor
                  floor={0}
                  author={topic.author}
                  body={topic.body}
                  createdAt={topic.createdAt}
                  threadReplies={[]}
                  expanded={false}
                  onToggleThreadReplies={() => undefined}
                  registerElement={registerReplyElement}
                />
              )}
              {topic.topicType === 'share' && topic.topicConfig?.share?.attachments.length ? (
                <section
                  aria-label="分享附件"
                  className="mb-4 rounded-xl border border-border bg-card p-4"
                >
                  <header className="flex items-center gap-2">
                    <Paperclip className="size-4 text-muted-foreground" aria-hidden />
                    <h3 className="m-0 text-sm font-semibold text-foreground">附件</h3>
                  </header>
                  <ul className="mt-3 space-y-2">
                    {topic.topicConfig.share.attachments.map((attachment) => (
                      <li
                        key={attachment.id}
                        className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2"
                      >
                        <div className="min-w-0 flex-1">
                          <p className="m-0 truncate text-sm font-medium text-foreground">
                            {attachment.filename}
                          </p>
                          <p className="m-0 text-xs text-muted-foreground">
                            {formatFileSize(attachment.sizeBytes)}
                          </p>
                        </div>
                        <Button
                          variant="outline"
                          size="sm"
                          leftIcon={<Download className="size-3.5" aria-hidden />}
                          onClick={() => void onDownloadAttachment(attachment)}
                        >
                          下载
                        </Button>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
              {topic.topicType === 'help' && topic.topicConfig?.help?.expectedTime ? (
                <section
                  aria-label="求助设置"
                  className="mb-4 flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-3"
                >
                  <CircleHelp className="size-4 text-warning" aria-hidden />
                  <span className="text-xs font-medium text-muted-foreground">期望解决时间</span>
                  <span className="text-sm text-foreground">{topic.topicConfig.help.expectedTime}</span>
                </section>
              ) : null}
              <PollPanel
                topicType={topic.topicType}
                status={topic.status}
                poll={topic.topicConfig?.poll}
                pollStats={topic.analysis?.pollStats}
              />
              {floorGroups.map(({ reply, floorNo, children }) => (
                <Floor
                  key={reply.id}
                  floor={floorNo}
                  postId={reply.id}
                  replyToPostId={reply.replyToPostId}
                  replyToFloorNo={reply.replyToFloorNo}
                  author={reply.author}
                  body={reply.body}
                  createdAt={reply.createdAt}
                  threadReplies={children}
                  expanded={!!expandedReplyIds[reply.id]}
                  onToggleThreadReplies={() => toggleThreadReplies(reply.id)}
                  onLocateTarget={locateTargetReply}
                  registerElement={registerReplyElement}
                />
              ))}
              {!replies.length && !replyLoading && <p className="py-6 text-center text-sm text-muted-foreground">暂时还没有回复</p>}
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
