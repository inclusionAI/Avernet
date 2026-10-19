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

  const handleSubmit = async () => {
    const nextErrors: DraftErrors = {
      ...(!title.trim() ? { title: '请输入主题标题' } : {}),
      ...(!body.trim() ? { body: '请输入主题正文' } : {}),
    };
    const options = pollOptions.map((option) => option.trim()).filter(Boolean);
    if (topicType === 'poll') {
      if (options.length < 2 || options.length > 20) {
        nextErrors.pollOptions =
          options.length < 2 ? '投票主题至少需要两个有效选项' : '投票选项最多 20 个';
      }
      if (new Set(options).size !== options.length) {
        nextErrors.pollOptions = '投票选项不能重复';
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
    <section className="app-scrollbar flex h-full flex-col overflow-y-auto" aria-label="发布主题编辑区">
      <header className="sticky top-0 z-10 flex items-center gap-2 border-b border-border bg-background/95 px-6 py-4 backdrop-blur">
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

      <div className="flex flex-1 flex-col gap-4 px-6 py-6">
        <div className="flex items-center">
          <Segmented<Exclude<CommunityTopicType, 'notice'>>
            value={topicType}
            onChange={setTopicType}
            aria-label="主题类型"
            options={[
              { value: 'discussion', label: '讨论' },
              { value: 'poll', label: '投票' },
            ]}
            className="h-10 w-72"
          />
        </div>

        <Input
          className="h-11 rounded-none border-x-0 border-t-0 border-border bg-transparent px-0 text-base font-semibold"
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

        <Textarea
          className="min-h-[260px] rounded-none border-x-0 border-t-0 border-border bg-transparent px-0 py-4 text-base leading-relaxed"
          value={body}
          onChange={(event) => {
            setBody(event.target.value);
            setErrors((value) => ({ ...value, body: undefined }));
          }}
          placeholder="输入正文内容..."
          maxLength={4000}
          variant={errors.body ? 'error' : 'default'}
          aria-label="正文"
          aria-invalid={!!errors.body}
        />
        {errors.body ? <p className="m-0 text-xs text-destructive">{errors.body}</p> : null}

        {topicType === 'poll' ? (
          <section aria-label="投票配置" className="rounded-xl border border-border bg-card p-5">
            <header className="flex items-center justify-between">
              <h3 className="m-0 text-sm font-semibold text-foreground">投票设置</h3>
            </header>

            <div className="mt-4 flex flex-col gap-2">
              {pollOptions.map((option, index) => (
                <div key={index} className="flex items-center gap-3">
                  <span className="w-8 text-sm font-medium tabular-nums text-muted-foreground">{index + 1}.</span>
                  <Input
                    className="h-10 flex-1 rounded-lg border-border bg-background px-3 text-sm"
                    value={option}
                    onChange={(event) => {
                      const next = [...pollOptions];
                      next[index] = event.target.value;
                      setPollOptions(next);
                      setErrors((value) => ({ ...value, pollOptions: undefined }));
                    }}
                    placeholder={`选项 ${index + 1}`}
                    maxLength={128}
                    aria-label={`选项 ${index + 1}`}
                    aria-invalid={!!errors.pollOptions}
                  />
                </div>
              ))}
              {errors.pollOptions ? <p className="m-0 text-xs text-destructive">{errors.pollOptions}</p> : null}

              <Button
                variant="ghost"
                size="sm"
                className="justify-start px-0 text-xs text-primary"
                disabled={pollOptions.length >= 20}
                onClick={() => setPollOptions([...pollOptions, ''])}
              >
                + 添加选项
              </Button>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-4">
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
              <div className="flex min-w-[220px] items-center gap-3">
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
                  className="h-9 w-56"
                  aria-invalid={!!errors.pollDeadline}
                />
              </div>
              {errors.pollDeadline ? <p className="m-0 text-xs text-destructive">{errors.pollDeadline}</p> : null}
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
