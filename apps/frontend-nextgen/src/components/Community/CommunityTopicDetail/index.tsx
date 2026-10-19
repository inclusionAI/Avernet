import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { Textarea } from '@/components/ui/Textarea';
import type { CommunityReply, CommunityTopic } from '@/domain/community/types';
import { ArrowLeft, User } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

interface CommunityThreadReply {
  reply: CommunityReply;
  targetAuthorName: string;
  targetPostId?: string;
}

interface CommunityFloorReplyGroup {
  reply: CommunityReply;
  floorNo: number;
  children: CommunityThreadReply[];
}

function indexReplies(replies: CommunityReply[]) {
  const replyById = new Map<string, CommunityReply>();
  replies.forEach((reply) => {
    replyById.set(reply.id, reply);
    reply.childReplies?.forEach((child) => replyById.set(child.id, child));
  });
  return replyById;
}

/**
 * Normalizes nested API payloads and the older flat page shape. The nested
 * payload is canonical for 楼中楼: `reply.childReplies` already contains every
 * descendant of that first-level floor. The flat path only remains as a
 * compatibility fallback for tests and clients provisioned before v2.
 */
function buildReplyGroups(
  replies: CommunityReply[],
  firstFloorNo: number,
): CommunityFloorReplyGroup[] {
  const replyById = indexReplies(replies);
  const targetName = (postId?: string) =>
    postId ? replyById.get(postId)?.author.displayName || '原回复' : '原回复';

  if (replies.some((reply) => reply.childReplies?.length)) {
    return replies.map((reply, index) => ({
      reply,
      floorNo: firstFloorNo + index,
      children: (reply.childReplies ?? []).map((child) => ({
        reply: child,
        targetAuthorName: targetName(child.replyToPostId),
        targetPostId: child.replyToPostId,
      })),
    }));
  }

  const groups: CommunityFloorReplyGroup[] = [];
  const groupByReplyId = new Map<string, CommunityFloorReplyGroup>();

  replies.forEach((reply, index) => {
    const target = reply.replyToPostId ? replyById.get(reply.replyToPostId) : undefined;
    const targetGroup = target ? groupByReplyId.get(target.id) : undefined;

    if (target && targetGroup) {
      targetGroup.children.push({
        reply,
        targetAuthorName: target.author.displayName,
        targetPostId: target.id,
      });
      groupByReplyId.set(reply.id, targetGroup);
      return;
    }

    const group: CommunityFloorReplyGroup = {
      reply,
      floorNo: firstFloorNo + index,
      children: [],
    };
    groups.push(group);
    groupByReplyId.set(reply.id, group);
  });

  return groups;
}

type ReplyElementRegistrar = (postId?: string) => (instance: HTMLElement | null) => void;

interface ThreadReplyProps {
  reply: CommunityReply;
  targetAuthorName: string;
  targetPostId?: string;
  onReply?: (postId: string) => void;
  onLocateTarget?: (postId: string) => void;
  registerElement?: ReplyElementRegistrar;
}

function ThreadReply({
  reply,
  targetAuthorName,
  targetPostId,
  onReply,
  onLocateTarget,
  registerElement,
}: ThreadReplyProps) {
  return (
    <article data-post-id={reply.id} ref={registerElement?.(reply.id)} className="rounded-xl border border-border/60 bg-muted/40 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <CommunityAuthorAvatar author={reply.author} size={24} />
        <span className="text-xs font-semibold text-foreground">{reply.author.displayName}</span>
        <span className="text-xs text-muted-foreground">回复</span>
        {targetPostId ? (
          <Button
            variant="link"
            size="sm"
            className="h-auto min-h-0 p-0 text-xs"
            aria-label={`定位${targetAuthorName}`}
            onClick={() => onLocateTarget?.(targetPostId)}
          >
            @{targetAuthorName}
          </Button>
        ) : (
          <span className="text-xs font-medium text-primary">@{targetAuthorName}</span>
        )}
      </div>
      <p className="m-0 mt-2 whitespace-pre-wrap text-xs leading-relaxed text-foreground">{reply.body}</p>
      <div className="mt-2 flex items-center justify-end gap-3">
        <span className="text-[10px] text-muted-foreground">{formatDateTime(reply.createdAt)}</span>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-[10px]"
          aria-label={`回复${reply.author.displayName}`}
          onClick={() => onReply?.(reply.id)}
        >
          回复
        </Button>
      </div>
    </article>
  );
}

interface FloorProps {
  floor: number;
  postId?: string;
  replyToPostId?: string;
  replyToFloorNo?: number;
  author: CommunityTopic['author'];
  body: string;
  createdAt: string;
  onReply?: (postId: string) => void;
  threadReplies: CommunityThreadReply[];
  expanded: boolean;
  onToggleThreadReplies: () => void;
  onLocateTarget?: (postId: string) => void;
  registerElement?: ReplyElementRegistrar;
}

// 一级楼层保持原布局；楼中楼只作为其下方的两层从属展示区。
function Floor({
  floor,
  postId,
  replyToPostId,
  replyToFloorNo,
  author,
  body,
  createdAt,
  onReply,
  threadReplies,
  expanded,
  onToggleThreadReplies,
  onLocateTarget,
  registerElement,
}: FloorProps) {
  const visibleThreadReplies = expanded ? threadReplies : threadReplies.slice(0, 2);
  const childListId = postId ? `${postId}-children` : undefined;

  return (
    <article data-post-id={postId} ref={registerElement?.(postId)} className="border-b border-border/60 py-4 last:border-b-0">
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
        {replyToPostId ? (
          <Badge tone="outline" className="h-6 rounded px-2 py-0.5 text-[10px]">
            {typeof replyToFloorNo === 'number' ? `回复 ${replyToFloorNo}楼` : '回复原回复'}
          </Badge>
        ) : null}
      </div>
      <div className="mt-2 pl-[44px] pr-[44px]">
        <div className="min-h-[120px]">
          <p className="m-0 whitespace-pre-wrap text-sm leading-relaxed text-foreground">{body}</p>
        </div>
        <div className="mt-2 flex items-center justify-between gap-3">
          <span className="text-xs text-muted-foreground">发表于：{formatDateTime(createdAt)}</span>
          {postId ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2.5 text-xs"
              aria-label={`回复第${floor}楼`}
              onClick={() => onReply?.(postId)}
            >
              回复
            </Button>
          ) : null}
        </div>
        {threadReplies.length > 0 ? (
          <div id={childListId} aria-label="楼中楼回复" className="mt-3 space-y-2 border-l border-border pl-3">
            {visibleThreadReplies.map(({ reply, targetAuthorName, targetPostId }) => (
              <ThreadReply
                key={reply.id}
                reply={reply}
                targetAuthorName={targetAuthorName}
                targetPostId={targetPostId}
                onReply={onReply}
                onLocateTarget={onLocateTarget}
                registerElement={registerElement}
              />
            ))}
            {threadReplies.length > 2 ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-full px-0 text-[10px] text-primary"
                aria-expanded={expanded}
                aria-controls={childListId}
                onClick={onToggleThreadReplies}
              >
                {expanded ? '收起 ▴' : `查看全部 ${threadReplies.length} 条回复 ▾`}
              </Button>
            ) : null}
          </div>
        ) : null}
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
  replyTargetPostId: string | null;
  replyPublishing: boolean;
  onStartReply: (postId: string) => void;
  onCancelReply: () => void;
  onSubmitReply: (body: string) => Promise<boolean>;
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
  replyTargetPostId,
  replyPublishing,
  onStartReply,
  onCancelReply,
  onSubmitReply,
}: CommunityTopicDetailProps) {
  const [replyDraft, setReplyDraft] = useState('');
  const [replySubmitError, setReplySubmitError] = useState<string | null>(null);
  const [expandedReplyIds, setExpandedReplyIds] = useState<Record<string, boolean>>({});
  const replyElementsRef = useRef(new Map<string, HTMLElement | null>());

  const replyOffset = (replyPage - 1) * replyPageSize;
  const floorGroups = useMemo(
    () => buildReplyGroups(replies, replyOffset + 1),
    [replies, replyOffset],
  );
  const floorByPostId = useMemo(() => {
    const floors = new Map<string, number>();
    floorGroups.forEach(({ reply, floorNo, children }) => {
      floors.set(reply.id, floorNo);
      children.forEach(({ reply: childReply }) => floors.set(childReply.id, floorNo));
    });
    return floors;
  }, [floorGroups]);
  const replyTargetFloor = replyTargetPostId ? floorByPostId.get(replyTargetPostId) ?? null : null;

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

  const submitReply = async () => {
    const body = replyDraft.trim();
    if (!body) {
      setReplySubmitError('请输入回复内容');
      return;
    }
    const submitted = await onSubmitReply(body);
    if (submitted) {
      setReplyDraft('');
      setReplySubmitError(null);
    }
  };

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
                  onReply={onStartReply}
                  threadReplies={children}
                  expanded={!!expandedReplyIds[reply.id]}
                  onToggleThreadReplies={() => toggleThreadReplies(reply.id)}
                  onLocateTarget={locateTargetReply}
                  registerElement={registerReplyElement}
                />
              ))}
              {topic.status === 'open' ? (
                <section className="py-4" aria-label="回复编辑区">
                  <div className="mb-2 flex items-center justify-between gap-3 text-xs text-muted-foreground">
                    <span>{replyTargetPostId ? `正在回复第 ${replyTargetFloor ?? '目标'} 楼` : '直接回复主题'}</span>
                    {replyTargetPostId ? (
                      <Button variant="ghost" size="sm" className="h-7 px-2.5" onClick={onCancelReply}>
                        取消引用
                      </Button>
                    ) : null}
                  </div>
                  <Textarea
                    className="min-h-[96px] rounded-lg border-border bg-card px-3 py-3 text-sm leading-relaxed"
                    value={replyDraft}
                    onChange={(event) => {
                      setReplyDraft(event.target.value);
                      setReplySubmitError(null);
                    }}
                    placeholder={replyTargetPostId ? '回复这个楼层...' : '回复这个主题...'}
                    maxLength={20000}
                    variant={replySubmitError ? 'error' : 'default'}
                    aria-label="回复正文"
                    aria-invalid={!!replySubmitError}
                  />
                  {replySubmitError ? <p className="m-0 mt-2 text-xs text-destructive">{replySubmitError}</p> : null}
                  <Button
                    className="mt-3 bg-foreground px-4 text-xs text-background hover:opacity-90"
                    loading={replyPublishing}
                    onClick={() => void submitReply()}
                  >
                    {replyTargetPostId ? '发送楼层回复' : '发送回复'}
                  </Button>
                </section>
              ) : null}
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
