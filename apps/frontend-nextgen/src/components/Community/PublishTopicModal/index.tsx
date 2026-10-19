import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
import { Textarea } from '@/components/ui/Textarea';
import type { CreateCommunityTopicDraft, CommunityPollSelection, CommunityTopicType } from '@/domain/community/types';
import { ArrowLeft } from 'lucide-react';
import { useEffect, useState } from 'react';

interface PublishTopicModalProps {
  open: boolean;
  publishing: boolean;
  onOpenChange: (open: boolean) => void;
  onPublish: (draft: CreateCommunityTopicDraft) => Promise<boolean>;
}

type DraftErrors = {
  title?: string;
  body?: string;
  pollOptions?: string;
  pollDeadline?: string;
};

/** Publish editor for discussion and poll Topics. */
export function PublishTopicModal({ open, publishing, onOpenChange, onPublish }: PublishTopicModalProps) {
  const [title, setTitle] = useState('');
  const [body, setBody] = useState('');
  const [topicType, setTopicType] = useState<Exclude<CommunityTopicType, 'notice'>>('discussion');
  const [pollSelection, setPollSelection] = useState<CommunityPollSelection>('single');
  const [pollOptions, setPollOptions] = useState('');
  const [pollDeadline, setPollDeadline] = useState('');
  const [errors, setErrors] = useState<DraftErrors>({});

  const resetDraft = () => {
    setTitle('');
    setBody('');
    setTopicType('discussion');
    setPollSelection('single');
    setPollOptions('');
    setPollDeadline('');
    setErrors({});
  };

  useEffect(() => {
    if (!open) resetDraft();
  }, [open]);

  const handleSubmit = async () => {
    const nextErrors: DraftErrors = {
      ...(!title.trim() ? { title: '请输入主题标题' } : {}),
      ...(!body.trim() ? { body: '请输入主题正文' } : {}),
    };
    const options = pollOptions
      .split('\n')
      .map((option) => option.trim())
      .filter(Boolean);
    if (topicType === 'poll') {
      if (options.length < 2 || new Set(options).size !== options.length) {
        nextErrors.pollOptions = options.length < 2 ? '投票主题至少需要两个不同选项' : '投票选项不能重复';
      }
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
    <section className="flex h-full flex-col" aria-label="发布主题编辑区">
      <header className="flex items-center gap-2">
        <Button variant="ghost" size="icon" onClick={() => onOpenChange(false)} aria-label="返回社区列表">
          <ArrowLeft className="size-4" aria-hidden />
        </Button>
        <h2 className="m-0 text-lg font-semibold text-foreground">发布主题</h2>
        <div className="ml-auto flex items-center gap-3">
          <Button variant="outline" size="sm" className="px-4 text-xs" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button size="sm" className="px-4 text-xs" loading={publishing} onClick={() => void handleSubmit()}>
            发布
          </Button>
        </div>
      </header>

      <div className="mt-4 flex flex-col gap-4">
        <Input
          className="h-10 rounded-lg border-border bg-card px-3 text-base font-semibold"
          value={title}
          onChange={(event) => {
            setTitle(event.target.value);
            setErrors((value) => ({ ...value, title: undefined }));
          }}
          placeholder="主题"
          maxLength={80}
          aria-label="主题标题"
          aria-invalid={!!errors.title}
        />
        {errors.title ? <p className="m-0 text-xs text-destructive">{errors.title}</p> : null}

        <Segmented<Exclude<CommunityTopicType, 'notice'>>
          value={topicType}
          onChange={setTopicType}
          aria-label="主题类型"
          options={[
            { value: 'discussion', label: '讨论' },
            { value: 'poll', label: '投票' },
          ]}
          className="h-9 w-56"
        />

        <Textarea
          className="min-h-[240px] rounded-lg border-border bg-card px-3 py-3 text-sm leading-relaxed"
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
            setErrors((value) => ({ ...value, body: undefined }));
          }}
          placeholder={topicType === 'poll' ? '说明投票背景和需要 Bot 给出的理由...' : '输入正文内容...'}
          maxLength={4000}
          variant={errors.body ? 'error' : 'default'}
          aria-label="正文"
          aria-invalid={!!errors.body}
        />
        {errors.body ? <p className="m-0 text-xs text-destructive">{errors.body}</p> : null}

        {topicType === 'poll' ? (
          <section aria-label="投票配置" className="rounded-xl border border-border bg-card p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="m-0 min-w-[64px] text-sm font-medium text-foreground">投票规则</h3>
              <Segmented<CommunityPollSelection>
                value={pollSelection}
                onChange={setPollSelection}
                aria-label="投票选择规则"
                options={[
                  { value: 'single', label: '单选' },
                  { value: 'multi', label: '多选' },
                ]}
                className="h-9 w-44"
              />
            </div>
            <Textarea
              className="mt-3 min-h-[120px] rounded-lg border-border bg-background px-3 py-3 text-sm leading-relaxed"
              value={pollOptions}
              onChange={(event) => {
                setPollOptions(event.target.value);
                setErrors((value) => ({ ...value, pollOptions: undefined }));
              }}
              placeholder={'每行一个投票选项，例如：\n方案A\n方案B'}
              aria-label="投票选项"
              aria-invalid={!!errors.pollOptions}
            />
            {errors.pollOptions ? <p className="m-0 mt-2 text-xs text-destructive">{errors.pollOptions}</p> : null}
            <div className="mt-3 flex flex-col gap-2">
              <label htmlFor="poll-deadline" className="text-xs font-medium text-muted-foreground">
                截止时间（可选）
              </label>
              <Input
                id="poll-deadline"
                type="datetime-local"
                value={pollDeadline}
                onChange={(event) => {
                  setPollDeadline(event.target.value);
                  setErrors((value) => ({ ...value, pollDeadline: undefined }));
                }}
                className="h-9 max-w-64"
                aria-invalid={!!errors.pollDeadline}
              />
              {errors.pollDeadline ? <p className="m-0 text-xs text-destructive">{errors.pollDeadline}</p> : null}
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
