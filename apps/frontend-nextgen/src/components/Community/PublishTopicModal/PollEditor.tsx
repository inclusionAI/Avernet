import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import type { CommunityPollSelection } from '@/domain/community/types';
import { MAX_COMMUNITY_POLL_OPTIONS } from '@/services/community/communityService';
import { Trash2 } from 'lucide-react';

interface PollEditorProps {
  selection: CommunityPollSelection;
  options: string[];
  deadline: string;
  optionsError?: string;
  deadlineError?: string;
  onSelectionChange: (selection: CommunityPollSelection) => void;
  onOptionsChange: (options: string[]) => void;
  onDeadlineChange: (deadline: string) => void;
  onClearOptionsError: () => void;
  onClearDeadlineError: () => void;
}

export function PollEditor({
  selection,
  options,
  deadline,
  optionsError,
  deadlineError,
  onSelectionChange,
  onOptionsChange,
  onDeadlineChange,
  onClearOptionsError,
  onClearDeadlineError,
}: PollEditorProps) {
  return (
    <section aria-label="投票配置" className="rounded-lg bg-card p-4">
      <header className="flex items-center justify-between">
        <h3 className="m-0 text-sm font-semibold text-foreground">投票设置</h3>
      </header>

      <div className="mt-4 grid grid-cols-1 items-baseline gap-x-2 gap-y-3 sm:grid-cols-[max-content_minmax(0,1fr)]">
        <span id="poll-type-label" className="text-sm">
          投票类型
        </span>
        <div role="radiogroup" aria-labelledby="poll-type-label" className="flex items-center gap-4">
          {(['single', 'multi'] as const).map((type) => (
            <label key={type} className="inline-flex cursor-pointer items-center gap-2 text-sm">
              <input
                type="radio"
                name="community-poll-type"
                className="size-4 accent-primary"
                checked={selection === type}
                onChange={() => onSelectionChange(type)}
              />
              {type === 'single' ? '单选' : '多选'}
            </label>
          ))}
        </div>
        <span className="text-sm">投票选项</span>
        <div className="flex min-w-0 flex-col gap-2">
          {options.map((option, index) => (
            <div key={index} className="flex items-center gap-3">
              <Input
                className="h-8 min-w-0 flex-1 rounded-lg border-border bg-background px-3 text-sm"
                value={option}
                onChange={(event) => {
                  const next = [...options];
                  next[index] = event.target.value;
                  onOptionsChange(next);
                  onClearOptionsError();
                }}
                placeholder={`选项${index + 1}`}
                maxLength={128}
                aria-label={`选项 ${index + 1}`}
                aria-invalid={!!optionsError}
              />
              {index >= 2 ? (
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`删除选项 ${index + 1}`}
                  onClick={() => onOptionsChange(options.filter((_, itemIndex) => itemIndex !== index))}
                >
                  <Trash2 className="size-4" aria-hidden />
                </Button>
              ) : null}
            </div>
          ))}
          {optionsError ? <p className="m-0 text-xs text-destructive">{optionsError}</p> : null}

          <Button
            variant="ghost"
            size="sm"
            className="self-start justify-start px-0 text-xs text-primary"
            disabled={options.length >= MAX_COMMUNITY_POLL_OPTIONS}
            onClick={() => onOptionsChange([...options, ''])}
          >
            + 添加选项
          </Button>
        </div>

        <label htmlFor="poll-deadline" className="text-sm">
          截止时间（可选）
        </label>
        <div>
          <Input
            id="poll-deadline"
            type="datetime-local"
            value={deadline}
            onChange={(event) => {
              onDeadlineChange(event.target.value);
              onClearDeadlineError();
            }}
            className="h-8 w-full max-w-56"
            aria-invalid={!!deadlineError}
          />
          {deadlineError ? <p className="m-0 text-xs text-destructive">{deadlineError}</p> : null}
        </div>
      </div>
    </section>
  );
}
