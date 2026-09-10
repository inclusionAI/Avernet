import { MESSAGE_VIEW_SCOPE_OPTIONS } from '@/domain/collaboration/messageViewScope';
import type { MessageViewScope } from '@/domain/collaboration/types';
import { useId } from 'react';

export interface MessageViewScopeFieldProps {
  value: MessageViewScope;
  onChange: (scope: MessageViewScope) => void;
  disabled?: boolean;
  label?: string;
}

/** 消息视角两选项 radio（表单 / 确认页场景；默认值由调用方给 full）。 */
export function MessageViewScopeField({ value, onChange, disabled, label = '消息视角' }: MessageViewScopeFieldProps) {
  const name = useId();
  return (
    <fieldset className="space-y-2 text-left" disabled={disabled}>
      <legend className="mb-2 text-xs font-medium text-foreground">{label}</legend>
      <div className="space-y-2" role="radiogroup" aria-label={label}>
        {MESSAGE_VIEW_SCOPE_OPTIONS.map((option) => (
          <label
            key={option.value}
            className="flex cursor-pointer items-start gap-2 rounded-lg border border-border px-3 py-2 has-[:checked]:border-primary has-[:checked]:bg-primary/5"
          >
            <input
              type="radio"
              name={name}
              className="mt-0.5 size-4 accent-primary"
              checked={value === option.value}
              disabled={disabled}
              onChange={() => onChange(option.value)}
            />
            <span className="min-w-0">
              <span className="block text-sm text-foreground">{option.label}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">{option.description}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
