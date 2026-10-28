import { Input } from '@/components/ui/Input';
import { CircleHelp } from 'lucide-react';

interface HelpEditorProps {
  expectedTime: string;
  error?: string;
  onChange: (expectedTime: string) => void;
}

export function HelpEditor({ expectedTime, error, onChange }: HelpEditorProps) {
  return (
    <section aria-label="求助设置" className="rounded-xl border border-border bg-card p-5">
      <header className="flex items-center gap-2">
        <CircleHelp className="size-4 text-warning" aria-hidden />
        <h3 className="m-0 text-sm font-semibold text-foreground">求助设置</h3>
      </header>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label htmlFor="help-expected-time" className="text-xs font-medium text-muted-foreground">
          期望解决时间（可选）
        </label>
        <Input
          id="help-expected-time"
          className="h-9 min-w-[260px] flex-1"
          value={expectedTime}
          onChange={(event) => onChange(event.target.value)}
          placeholder="如：希望本周内"
          maxLength={128}
          aria-invalid={!!error}
        />
      </div>
      {error ? <p className="m-0 mt-2 text-xs text-destructive">{error}</p> : null}
    </section>
  );
}
