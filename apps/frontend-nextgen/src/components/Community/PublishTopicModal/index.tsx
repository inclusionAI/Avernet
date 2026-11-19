import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import type {
  CommunityPollSelection,
  CommunityPublishTopicType,
  CreateCommunityTopicDraft,
} from '@/domain/community/types';
import { MAX_COMMUNITY_POLL_OPTIONS } from '@/services/community/communityService';
import { ArrowLeft } from 'lucide-react';
import { useEffect, useState } from 'react';
import { PollEditor } from './PollEditor';
import { RichTextEditor } from './RichTextEditor';
import type { DraftErrors } from './model';

interface PublishTopicModalProps {
  open: boolean;
  publishing: boolean;
  onOpenChange: (open: boolean) => void;
  onPublish: (draft: CreateCommunityTopicDraft) => Promise<boolean>;
}

const topicTypeOptions = [
  { value: 'discussion' as const, label: '讨论' },
  { value: 'share' as const, label: '分享' },
  { value: 'poll' as const, label: '投票' },
  { value: 'help' as const, label: '求助' },
];

/** 发布讨论、分享、投票和求助主题。 */
export function PublishTopicModal({ open, publishing, onOpenChange, onPublish }: PublishTopicModalProps) {
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
    if (publishing) return;
    const nextErrors: DraftErrors = {
      ...(!title.trim() ? { title: '请输入主题标题' } : {}),
      ...(!body.trim() ? { body: '请输入主题正文' } : body.length > 4000 ? { body: '正文最多 4000 字符' } : {}),
    };
    const options = pollOptions.map((option) => option.trim()).filter(Boolean);
    if (topicType === 'poll') {
      if (options.length < 2 || options.length > MAX_COMMUNITY_POLL_OPTIONS) {
        nextErrors.pollOptions =
          options.length < 2 ? '投票主题至少需要两个有效选项' : `投票选项最多 ${MAX_COMMUNITY_POLL_OPTIONS} 个`;
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
    <section className="flex h-full min-h-0 flex-col bg-muted/40" aria-label="发布主题编辑区">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-divider bg-background px-5 text-sm">
        <Button
          variant="ghost"
          size="icon"
          disabled={publishing}
          onClick={() => onOpenChange(false)}
          aria-label="返回社区列表"
        >
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <span className="text-muted-foreground">实验室　›　社区　›</span>
        <h2 className="m-0 text-sm font-medium">发帖</h2>
      </header>

      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex min-h-full w-full max-w-[840px] flex-col gap-2 p-5">
        <Segmented<CommunityPublishTopicType>
          value={topicType}
          onChange={(next) => {
            if (!publishing) setTopicType(next);
          }}
          aria-label="主题类型"
          options={topicTypeOptions}
          className="mb-3 h-8 w-fit self-center"
        />
        <Input
          className="h-10 shrink-0 rounded-lg border border-border bg-background px-3 text-base font-semibold"
          value={title}
          onChange={(event) => {
            setTitle(event.target.value);
            clearError('title');
          }}
          placeholder="主题"
          disabled={publishing}
          maxLength={80}
          aria-label="主题标题"
          aria-invalid={!!errors.title}
        />
        {errors.title ? <p className="m-0 text-xs text-destructive">{errors.title}</p> : null}
        <RichTextEditor
          value={body}
          onChange={(next) => {
            setBody(next);
            clearError('body');
          }}
          invalid={!!errors.body}
          disabled={publishing}
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
      </main>
      <footer className="flex min-h-16 shrink-0 items-center justify-center gap-3 border-t border-divider bg-background px-5 py-3">
        <Button variant="outline" disabled={publishing} onClick={() => onOpenChange(false)}>
          取消
        </Button>
        <Button loading={publishing} disabled={publishing || body.length > 4000} onClick={() => void handleSubmit()}>
          发布
        </Button>
      </footer>
    </section>
  );
}
