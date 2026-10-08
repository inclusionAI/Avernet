import { Input } from '@/components/ui/Input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import type { BotEditorModel, BotEditorRoutineInput } from '@/domain/botEditor';
import { Loader2 } from 'lucide-react';

export function RoutineExecutionEnvironment({
  form,
  models,
  loading,
  error,
  allowDefaultModel,
  onChange,
}: {
  form: BotEditorRoutineInput;
  models: BotEditorModel[];
  loading: boolean;
  error: string;
  allowDefaultModel: boolean;
  onChange: (next: BotEditorRoutineInput) => void;
}) {
  return (
    <div className="border-t border-border pt-4">
      <p className="m-0 mb-3 text-sm font-medium text-foreground">执行环境</p>
      <div className="grid gap-4 sm:grid-cols-3">
        <label className="block text-sm">
          模型
          <Select
            value={form.model || '__bot_default__'}
            disabled={loading}
            onValueChange={(model) => onChange({ ...form, model: model === '__bot_default__' ? undefined : model })}
          >
            <SelectTrigger className="mt-1" aria-label="执行模型">
              <SelectValue placeholder="选择模型" />
            </SelectTrigger>
            <SelectContent>
              {allowDefaultModel ? <SelectItem value="__bot_default__">跟随 Bot 默认模型</SelectItem> : null}
              {models.map((model) => (
                <SelectItem key={model.id} value={model.id}>
                  {model.name}
                  {model.provider ? ` · ${model.provider}` : ''}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {loading ? (
            <span className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
              <Loader2 className="size-3 animate-spin" />
              正在加载模型…
            </span>
          ) : error ? (
            <span className="mt-1 block text-xs text-destructive">{error}</span>
          ) : null}
        </label>
        <label className="block text-sm">
          时区
          <Select
            value={form.timezone || 'Asia/Shanghai'}
            onValueChange={(timezone) => onChange({ ...form, timezone })}
          >
            <SelectTrigger className="mt-1" aria-label="执行时区">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="Asia/Shanghai">Asia/Shanghai</SelectItem>
              <SelectItem value="UTC">UTC</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <label className="block text-sm">
          超时时间（秒）
          <Input
            aria-label="超时时间（秒）"
            className="mt-1"
            type="number"
            min={1}
            value={form.timeoutSecs}
            onChange={(event) => onChange({ ...form, timeoutSecs: Number(event.target.value) })}
          />
        </label>
      </div>
    </div>
  );
}
