import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { CommunityTopic } from '@/domain/community/types';
import { ArrowLeft, MessageSquare, Share2 } from 'lucide-react';
import { PollTypeIcon } from '../PollTypeIcon';

export interface TopicDetailHeaderProps {
  topic: CommunityTopic | null;
  closing: boolean;
  deleting: boolean;
  replyLoading: boolean;
  replyPage: number;
  replyPageCount: number;
  currentUserId?: string;
  onBack: () => void;
  onCloseTopic: () => Promise<boolean>;
  onEditTopic: () => void;
  onDeleteTopic: () => Promise<boolean>;
  onGoToReplyPage: (page: number) => void;
  onGoToReplyComposer: () => void;
}

export function TopicDetailHeader({
  topic,
  closing,
  deleting,
  replyLoading,
  replyPage,
  replyPageCount,
  currentUserId,
  onBack,
  onCloseTopic,
  onEditTopic,
  onDeleteTopic,
  onGoToReplyPage,
  onGoToReplyComposer,
}: TopicDetailHeaderProps) {
  const isHelpTopic = topic?.topicType === 'help';

  return (
    <header className="flex w-full items-start gap-3 px-4 pb-4 pt-6 sm:px-6 lg:px-10">
      <Button
        variant="ghost"
        size="icon"
        onClick={onBack}
        aria-label="返回社区列表"
        className="mt-1 shrink-0"
      >
        <ArrowLeft className="size-4" aria-hidden />
      </Button>
      <div className="min-w-0 flex-1">
        <h2 className="m-0 min-w-0 truncate text-lg font-semibold text-foreground">
          {topic?.title || '主题详情'}
        </h2>
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
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <Share2 className="size-4" aria-hidden />
              分享
            </span>
          )}
          {topic?.topicType === 'help' && <Badge tone="warning">求助</Badge>}
          {topic?.topicType === 'notice' && <Badge tone="warning">通知</Badge>}
          {topic?.status === 'closed' && topic.topicType === 'help' && (
            <Badge tone="success">已解决</Badge>
          )}
          {topic?.status === 'closed' && topic.topicType !== 'help' && (
            <Badge tone="outline">已结帖</Badge>
          )}
          {topic?.canEdit ? (
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-3 text-xs"
              onClick={onEditTopic}
            >
              编辑
            </Button>
          ) : null}
          {topic?.canDelete ? (
            <ConfirmDialog
              title="确认删除主题？"
              description="删除后该主题及其全部回复将从社区中移除，且无法恢复。"
              confirmText="删除"
              confirmVariant="destructive"
              loading={deleting}
              onConfirm={async () => {
                await onDeleteTopic();
              }}
            >
              <Button variant="destructive" size="sm" className="h-8 px-3 text-xs">
                删除
              </Button>
            </ConfirmDialog>
          ) : null}
          {topic?.canClose && (
            <ConfirmDialog
              title={isHelpTopic ? '确认标记为已解决？' : '确认结帖？'}
              description={
                isHelpTopic
                  ? '标记后该求助将显示为已解决，本期暂不支持重新打开。'
                  : '结帖后该主题将显示为已结束，本期暂不支持重新打开。'
              }
              confirmText={isHelpTopic ? '确认标记' : '确认结帖'}
              loading={closing}
              onConfirm={async () => {
                await onCloseTopic();
              }}
            >
              <Button variant="outline" size="sm" className="h-8 px-3 text-xs">
                {isHelpTopic ? '标记已解决' : '结帖'}
              </Button>
            </ConfirmDialog>
          )}
          {topic?.status === 'open' && currentUserId && topic.topicType !== 'notice' ? (
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-3 text-xs"
              leftIcon={<MessageSquare className="size-3.5" aria-hidden />}
              onClick={onGoToReplyComposer}
            >
              回复
            </Button>
          ) : null}
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
  );
}
