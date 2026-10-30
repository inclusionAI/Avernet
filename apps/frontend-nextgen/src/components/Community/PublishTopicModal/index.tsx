import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import { Textarea } from '@/components/ui/Textarea';
import type {
  CommunityPollSelection,
  CommunityPublishTopicType,
  CreateCommunityTopicDraft,
} from '@/domain/community/types';
import { MAX_COMMUNITY_POLL_OPTIONS } from '@/services/community/communityService';
import { ArrowLeft } from 'lucide-react';
import { TopicTypeIcon } from '../TopicTypeIcon';
import { useEffect, useState } from 'react';
import { PollEditor } from './PollEditor';
import type { DraftErrors } from './model';

interface PublishTopicModalProps {
  open: boolean;
  publishing: boolean;
  onOpenChange: (open: boolean) => void;
  onPublish: (draft: CreateCommunityTopicDraft) => Promise<boolean>;
}

const topicTypeOptions = [
  { value: 'discussion' as const, label: '讨论', icon: <TopicTypeIcon topicType="discussion" className="size-3.5" /> },
  { value: 'share' as const, label: '分享', icon: <TopicTypeIcon topicType="share" className="size-3.5" /> },
  { value: 'poll' as const, label: '投票', icon: <TopicTypeIcon topicType="poll" className="size-3.5" /> },
  { value: 'help' as const, label: '求助', icon: <TopicTypeIcon topicType="help" className="size-3.5" /> },
];

/** 发布讨论、分享、投票和求助主题。 */
export function PublishTopicModal({
  open,
  publishing,
  onOpenChange,
  onPublish,
}: PublishTopicModalProps) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [topicType, setTopicType] = useState<CommunityPublishTopicType>('discussion');
  const [pollSelection, setPollSelection] = useState<CommunityPollSelection>('single');
  const [pollOptions, setPollOptions] = useState<string[]>(['', '']);
  const [pollDeadline, setPollDeadline] = useState('');
  const [errors, setErrors] = useState<DraftErrors>({});

  const resetDraft = () => {
    setTitle('');
    setBody('');
    setTopicType('discussion');
    setPollSelection('single');
    setPollOptions(['', '']);
    setPollDeadline('');
    setErrors({});
  };

  useEffect(() => {
    if (!open) resetDraft();
  }, [open]);

  const clearError = (key: keyof DraftErrors) => {
    setErrors((current) => ({ ...current, [key]: undefined }));
  };

  const handleSubmit = async () => {
    const nextErrors: DraftErrors = {
      ...(!title.trim() ? { title: '请输入主题标题' } : {}),
      ...(!body.trim() ? { body: '请输入主题正文' } : {}),
    };
    const options = pollOptions.map((option) => option.trim()).filter(Boolean);
    if (topicType === 'poll') {
      if (options.length < 2 || options.length > MAX_COMMUNITY_POLL_OPTIONS) {
        nextErrors.pollOptions =
          options.length < 2
            ? '投票主题至少需要两个有效选项'
            : `投票选项最多 ${MAX_COMMUNITY_POLL_OPTIONS} 个`;
      }
      if (new Set(options).size !== options.length) nextErrors.pollOptions = '投票选项不能重复';
      if (pollDeadline && Number.isNaN(new Date(pollDeadline).getTime())) {
        nextErrors.pollDeadline = '请输入有效的截止时间';
      }
    }

    setErrors(nextErrors);
    if (Object.keys(nextErrors).length) return;
    const draft: CreateCommunityTopicDraft = {
      title,
      body,
      topicType,
      ...(topicType === 'poll'
        ? {
            poll: {
              selection: pollSelection,
              options,
              ...(pollDeadline ? { deadlineAt: new Date(pollDeadline).toISOString() } : {}),
            },
          }
        : {}),
    };
    if (await onPublish(draft)) onOpenChange(false);
  };

  if (!open) return null;
  return (
    <section className="app-scrollbar flex h-full flex-col overflow-y-auto" aria-label="发布主题编辑区">
      <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-divider bg-background/95 px-6 py-4 backdrop-blur">
        <Button variant="ghost" size="icon" onClick={() => onOpenChange(false)} aria-label="返回社区列表">
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <h2 className="m-0 text-lg font-semibold text-foreground">发布主题</h2>
        <div className="ml-auto flex items-center gap-3">
          <Button variant="outline" size="sm" className="px-4 text-xs" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            size="sm"
            className="px-4 text-xs"
            loading={publishing}
            onClick={() => void handleSubmit()}
          >
            发布
          </Button>
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-4 px-6 py-6">
        <Segmented<CommunityPublishTopicType>
          value={topicType}
          onChange={setTopicType}
          aria-label="主题类型"
          options={topicTypeOptions}
          className="h-10 w-full max-w-md"
        />
        <Input
          className="h-11 rounded-lg border-border bg-background px-3 text-base font-semibold"
          value={title}
          onChange={(event) => {
            setTitle(event.target.value);
            clearError('title');
          }}
          placeholder="主题"
          maxLength={80}
          aria-label="主题标题"
          aria-invalid={!!errors.title}
        />
        {errors.title ? <p className="m-0 text-xs text-destructive">{errors.title}</p> : null}
        <Textarea
          className="min-h-[260px] rounded-lg border-border bg-background px-3 py-4 text-base leading-relaxed"
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
            clearError('body');
          }}
          placeholder="输入正文内容..."
          maxLength={4000}
          variant={errors.body ? 'error' : 'default'}
          aria-label="正文"
          aria-invalid={!!errors.body}
        />
        {errors.body ? <p className="m-0 text-xs text-destructive">{errors.body}</p> : null}

        {topicType === 'poll' ? (
          <PollEditor
            selection={pollSelection}
            options={pollOptions}
            deadline={pollDeadline}
            optionsError={errors.pollOptions}
            deadlineError={errors.pollDeadline}
            onSelectionChange={setPollSelection}
            onOptionsChange={setPollOptions}
            onDeadlineChange={setPollDeadline}
            onClearOptionsError={() => clearError('pollOptions')}
            onClearDeadlineError={() => clearError('pollDeadline')}
          />
        ) : null}
      </div>
    </section>
  );
}
