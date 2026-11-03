import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Segmented } from '@/components/ui/Segmented';
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
    <section aria-label="投票配置" className="rounded-xl border border-border bg-card p-5">
      <header className="flex items-center justify-between">
        <h3 className="m-0 text-sm font-semibold text-foreground">投票设置</h3>
      </header>

      <div className="mt-4 flex flex-col gap-2">
        {options.map((option, index) => (
          <div key={index} className="flex items-center gap-3">
            <span className="w-8 text-sm font-medium tabular-nums text-muted-foreground">{index + 1}.</span>
            <Input
              className="h-10 flex-1 rounded-lg border-border bg-background px-3 text-sm"
              value={option}
              onChange={(event) => {
                const next = [...options];
                next[index] = event.target.value;
                onOptionsChange(next);
                onClearOptionsError();
              }}
              placeholder={`选项 ${index + 1}`}
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
          className="justify-start px-0 text-xs text-primary"
          disabled={options.length >= MAX_COMMUNITY_POLL_OPTIONS}
          onClick={() => onOptionsChange([...options, ''])}
        >
          + 添加选项
        </Button>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-4">
        <Segmented<CommunityPollSelection>
          value={selection}
          onChange={onSelectionChange}
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
            value={deadline}
            onChange={(event) => {
              onDeadlineChange(event.target.value);
              onClearDeadlineError();
            }}
            className="h-9 w-56"
            aria-invalid={!!deadlineError}
          />
        </div>
        {deadlineError ? <p className="m-0 text-xs text-destructive">{deadlineError}</p> : null}
      </div>
    </section>
  );
}
