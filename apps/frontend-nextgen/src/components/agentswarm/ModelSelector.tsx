import { Input } from '@/components/ui/Input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/Select';
import { INHERIT_VALUE, KNOWN_MODELS, isKnownModel } from '@/domain/agentswarm/models';

export interface ModelSelectorProps {
  value?: string | null;
  onChange: (value: string | null) => void;
  /** Wording for the "fall through" choice, e.g. inherit the run default. */
  inheritLabel: string;
  label: string;
  hint?: string;
}

/**
 * Model choice offering known names plus a typed one.
 *
 * There is no model catalogue on this surface, so a closed list would exclude a
 * deployment's newer model; a typed value is therefore first-class rather than
 * an escape hatch.
 */
export function ModelSelector({ value, onChange, inheritLabel, label, hint }: ModelSelectorProps) {
  const current = value?.trim() ?? '';
  const isCustom = Boolean(current) && !isKnownModel(current);

  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-foreground">{label}</span>
      <Select
        value={isCustom ? '__custom__' : current || INHERIT_VALUE}
        onValueChange={(next) => {
          if (next === INHERIT_VALUE) onChange(null);
          else if (next === '__custom__') onChange('');
          else onChange(next);
        }}
      >
        <SelectTrigger>
          <SelectValue placeholder={inheritLabel} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={INHERIT_VALUE}>{inheritLabel}</SelectItem>
          {KNOWN_MODELS.map((model) => (
            <SelectItem key={model} value={model}>
              {model}
            </SelectItem>
          ))}
          <SelectItem value="__custom__">自定义模型…</SelectItem>
        </SelectContent>
      </Select>
      {isCustom || current === '' ? (
        <Input
          value={current}
          placeholder="输入模型名称"
          onChange={(event) => onChange(event.target.value)}
        />
      ) : null}
      {hint ? <span className="text-[11px] text-muted-foreground">{hint}</span> : null}
    </div>
  );
}