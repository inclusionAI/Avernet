import { IDENTITY_PICKER_HINT, type IdentityOption } from '@/domain/agentswarm/identityOptions';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/Select';
import { LoaderCircle } from 'lucide-react';
import { useMemo } from 'react';

export interface IdentityBotPickerProps {
  options: IdentityOption[];
  value?: string | null;
  onChange: (botId: string) => void;
  loading?: boolean;
  error?: string | null;
}

/**
 * Run-level identity source for an Agent Compute run.
 *
 * One selection for the whole run — the gateway carries `identity_bot_id` once
 * on the submit, not per agent. The caller renders this only when the run
 * actually provisions Bots, so there is no exemption branch to carry here.
 */
export function IdentityBotPicker({
  options,
  value,
  onChange,
  loading = false,
  error = null,
}: IdentityBotPickerProps) {

  const selectableOptions = options.filter((option) => option.selectable);
  const selectableCount = selectableOptions.length;
  const missing = !value;
  // A seeded default (the chat's own Bot) may not be in the fetched list yet, or
  // may be filtered out; keep it visible so the current choice is never blank.
  const valueIsListed = useMemo(
    () => Boolean(value) && selectableOptions.some((option) => option.botId === value),
    [selectableOptions, value],
  );
  const currentOptions = useMemo(
    () =>
      value && !valueIsListed
        ? [{ botId: value, displayName: value, online: true, selectable: true } as IdentityOption, ...selectableOptions]
        : selectableOptions,
    [selectableOptions, value, valueIsListed],
  );

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <label className="text-xs font-medium text-foreground" htmlFor="agentswarm-identity-bot">
          身份 Bot <span className="text-destructive">*</span>
        </label>
        {loading ? <LoaderCircle className="h-4 w-4 animate-spin text-primary" aria-hidden /> : null}
      </div>

      <p className="text-xs text-muted-foreground">{IDENTITY_PICKER_HINT}</p>

      {error ? (
        <p className="text-xs text-destructive">{error}</p>
      ) : selectableCount === 0 && !loading ? (
        <p className="text-xs text-muted-foreground">
          你还没有可用的 Bot。请先在 Bot 管理中创建一个，再回来发起计算任务。
        </p>
      ) : (
        <Select value={value || undefined} onValueChange={onChange} disabled={loading}>
          <SelectTrigger id="agentswarm-identity-bot" aria-invalid={missing || undefined}>
            {/* 显式渲染当前值：Radix 只从 options 里取 label，选中的 Bot
                若不在本次列表（例如刚切换会话），会退化成占位文案。 */}
            <SelectValue placeholder="选择用于继承身份的运行 Bot">
              {value ? currentOptions.find((option) => option.botId === value)?.displayName ?? value : undefined}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {/* Only selectable Bots are listed: a disabled row is noise the user
                cannot act on. */}
            {currentOptions.map((option) => (
              <SelectItem key={option.botId} value={option.botId}>
                <span className="truncate">{option.displayName}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  );
}