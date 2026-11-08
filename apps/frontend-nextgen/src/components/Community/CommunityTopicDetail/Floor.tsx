import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { CommunityTopic, CreateCommunityReplyDraft, InteractionSummary } from '@/domain/community/types';
import { MessageSquare, User } from 'lucide-react';
import { useState } from 'react';
import { CommunityAuthorAvatar } from '../CommunityAuthorAvatar';
import { formatDateTime, type CommunityThreadReply, type ReplyElementRegistrar } from './model';
import { ReplyComposer, type ReplyComposerTarget } from './ReplyComposer';
import { ThreadReply } from './ThreadReply';

interface FloorProps {
  topic: CommunityTopic;
  floor: number;
  postId?: string;
  replyToPostId?: string;
  replyToFloorNo?: number;
  author: CommunityTopic['author'];
  body: string;
  createdAt: string;
  helpful?: boolean;
  canToggleHelpful?: boolean;
  helpfulLoading?: boolean;
  onToggleHelpful?: () => void;
  threadReplies: CommunityThreadReply[];
  expanded: boolean;
  onToggleThreadReplies: () => void;
  onLocateTarget?: (postId: string) => void;
  registerElement?: ReplyElementRegistrar;
  currentUserId?: string;
  getAuthorSummary?: (author: CommunityTopic['author']) => InteractionSummary | undefined;
  loadAuthorSummary?: (author: CommunityTopic['author']) => void;
  followAuthor?: (author: CommunityTopic['author']) => Promise<boolean>;
  authorFollowLoading?: (author: CommunityTopic['author']) => boolean;
  canReply?: boolean;
  replySaving?: boolean;
  onSubmitReply?: (payload: CreateCommunityReplyDraft) => Promise<boolean>;
}

// 一级楼层保持原布局；楼中楼只作为其下方的两层从属展示区。
export function Floor({
  topic,
  floor,
  postId,
  replyToPostId,
  replyToFloorNo,
  author,
  body,
  createdAt,
  helpful = false,
  canToggleHelpful = false,
  helpfulLoading = false,
  onToggleHelpful,
  threadReplies,
  expanded,
  onToggleThreadReplies,
  onLocateTarget,
  registerElement,
  currentUserId,
  getAuthorSummary,
  loadAuthorSummary,
  followAuthor,
  authorFollowLoading,
  canReply = false,
  replySaving = false,
  onSubmitReply,
}: FloorProps) {
  const [replyTarget, setReplyTarget] = useState<ReplyComposerTarget | null>(null);
  const visibleThreadReplies = expanded ? threadReplies : threadReplies.slice(0, 2);
  const childListId = postId ? `${postId}-children` : undefined;

  const submitReply = async (payload: CreateCommunityReplyDraft) => {
    const succeeded = await onSubmitReply?.(payload);
    if (succeeded) setReplyTarget(null);
    return succeeded === true;
  };

  return (
    <article data-post-id={postId} ref={registerElement?.(postId)} className="border-b border-border/60 py-4 last:border-b-0">
      <div className="flex items-center gap-2">
        <CommunityAuthorAvatar
          author={author}
          size={36}
          currentUserId={currentUserId}
          summary={getAuthorSummary?.(author)}
          onLoadSummary={() => loadAuthorSummary?.(author)}
          onFollow={() => void followAuthor?.(author)}
          followLoading={authorFollowLoading?.(author) === true}
        />
        <span className="text-sm font-semibold text-foreground">{author.displayName}</span>
        <Badge
          tone={floor === 0 ? 'primary' : 'neutral'}
          className={
            floor === 0
              ? 'gap-1 rounded bg-foreground px-1.5 py-0.5 text-[10px] text-background'
              : 'h-6 rounded px-2 py-0.5 text-[10px]'
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
        {floor > 0 && helpful ? <Badge tone="success">有帮助</Badge> : null}
      </div>
      <div className="mt-2 pl-[44px] pr-[44px]">
        <div className="min-h-[120px]">
          <p className="m-0 whitespace-pre-wrap text-sm leading-relaxed text-foreground">{body}</p>
        </div>
        <div className="mt-2 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {canReply && postId && onSubmitReply ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-[11px] text-primary"
                leftIcon={<MessageSquare className="size-3.5" aria-hidden />}
                onClick={() => setReplyTarget({ postId, authorName: author.displayName })}
              >
                回复
              </Button>
            ) : null}
            {floor > 0 && canToggleHelpful && onToggleHelpful ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-[11px] text-primary"
                loading={helpfulLoading}
                onClick={onToggleHelpful}
              >
                {helpful ? '取消有帮助' : '标记有帮助'}
              </Button>
            ) : null}
          </div>
          <span className="text-xs text-muted-foreground">发表于：{formatDateTime(createdAt)}</span>
        </div>
        {threadReplies.length > 0 ? (
          <div id={childListId} aria-label="楼中楼回复" className="mt-3 space-y-2 border-l border-border pl-3">
            {visibleThreadReplies.map(({ reply, targetAuthorName, targetPostId }) => (
              <ThreadReply
                key={reply.id}
                reply={reply}
                targetAuthorName={targetAuthorName}
                targetPostId={targetPostId}
                onLocateTarget={onLocateTarget}
                registerElement={registerElement}
                currentUserId={currentUserId}
                getAuthorSummary={getAuthorSummary}
                loadAuthorSummary={loadAuthorSummary}
                followAuthor={followAuthor}
                authorFollowLoading={authorFollowLoading}
                canReply={canReply}
                onReply={setReplyTarget}
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
        {canReply && replyTarget && onSubmitReply ? (
          <div className="mt-3">
            <ReplyComposer
              topic={topic}
              submitLabel="回复楼层"
              saving={replySaving}
              replyTo={replyTarget}
              onSubmit={submitReply}
              onCancel={() => setReplyTarget(null)}
            />
          </div>
        ) : null}
      </div>
    </article>
  );
}
