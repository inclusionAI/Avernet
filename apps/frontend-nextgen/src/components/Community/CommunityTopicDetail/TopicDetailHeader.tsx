import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import type { CommunityTopic } from '@/domain/community/types';
import { ArrowLeft, CircleAlert, Lock, Trash2 } from 'lucide-react';

export interface TopicDetailHeaderProps {
  topic: CommunityTopic | null;
  closing: boolean;
  deleting: boolean;
  onBack: () => void;
  onCloseTopic: () => Promise<boolean>;
  onDeleteTopic: () => Promise<boolean>;
}

export function TopicDetailHeader({
  topic,
  closing,
  deleting,
  onBack,
  onCloseTopic,
  onDeleteTopic,
}: TopicDetailHeaderProps) {
  const isHelpTopic = topic?.topicType === 'help';

  return (
    <header className="w-full px-5 pb-4 pt-5">
      <div className="mb-5 flex items-center gap-2 border-b border-divider pb-3">
        <Button variant="ghost" size="icon" onClick={onBack} aria-label="返回社区列表" className="shrink-0">
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <p className="m-0 text-xs text-muted-foreground">实验室　›　社区　›　帖子详情</p>
      </div>
      <div className="flex items-center justify-between gap-4" aria-label="帖子标题与管理操作">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <h2 className="m-0 min-w-0 break-words text-lg font-semibold text-foreground">
            {topic?.title || '主题详情'}
          </h2>
          {topic?.status === 'closed' && topic.topicType !== 'help' && <Badge tone="outline">已结帖</Badge>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            {topic?.status === 'closed' && topic.topicType === 'help' && <Badge tone="success">已解决</Badge>}

            {topic?.canDelete ? (
              <ConfirmDialog
                title={
                  <span className="flex items-center gap-3">
                    <CircleAlert className="size-6 shrink-0 text-warning" aria-hidden />
                    确认要删除这条帖子吗？
                  </span>
                }
                description="删除后将无法恢复，同时评论及回复一起被删除。"
                confirmText="确定"
                confirmVariant="primary"
                loading={deleting}
                onConfirm={async () => {
                  await onDeleteTopic();
                }}
              >
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<Trash2 className="size-3.5" />}
                  className="h-8 border-destructive/30 px-3 text-xs text-destructive"
                >
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
                <Button
                  variant="outline"
                  size="sm"
                  leftIcon={<Lock className="size-3.5" />}
                  className="h-8 border-primary/30 px-3 text-xs text-primary"
                >
                  {isHelpTopic ? '标记已解决' : '结帖'}
                </Button>
              </ConfirmDialog>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
