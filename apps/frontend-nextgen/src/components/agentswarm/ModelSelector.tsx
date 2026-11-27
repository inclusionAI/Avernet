import { Input } from '@/components/ui/Input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/Select';
import { KNOWN_MODELS, normalizeModelId } from '@/domain/agentswarm/models';

export interface ModelSelectorProps {
  value?: string | null;
  onChange: (value: string | null) => void;
  /** Wording for the "fall through" choice, e.g. inherit the run default. */
  inheritLabel: string;
  label: string;
  hint?: string;
  /** Wording for the typed-name choice, default 「自定义模型…」. */
  customLabel?: string;
  /** Placeholder for the typed name, shown only once a custom name is chosen. */
  customPlaceholder?: string;
  /**
   * The models actually offered, normally a Bot's own catalogue. Absent or
   * empty falls back to the static list, which exists for surfaces that have no
   * Bot to ask (and for the gap before a catalogue read returns).
   */
  models?: readonly string[];
}

const FOLLOW = '__follow__';
const CUSTOM = '__custom__';

/**
 * Model choice: follow the next level, or name one.
 *
 * A stored value is used verbatim: it has to be an id the Bot can resolve, and
 * the catalogue's ids are already provider-qualified. Matching for the dropdown
 * ignores the provider prefix only so an older bare-name entry still shows as
 * the model it names; nothing is rewritten on the way out.
 */
export function ModelSelector({
  value,
  onChange,
  inheritLabel,
  label,
  hint,
  customLabel = '自定义模型…',
  customPlaceholder = '输入模型名称',
  models,
}: ModelSelectorProps) {
  const offered = models && models.length > 0 ? models : KNOWN_MODELS;
  const current = value?.trim() ?? '';
  const bare = bareName(current);
  const isKnown = Boolean(bare) && offered.some((model) => bareName(model) === bare);
  const isCustom = Boolean(current) && !isKnown;

  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-foreground">{label}</span>
      <Select
        value={isCustom ? CUSTOM : current || FOLLOW}
        onValueChange={(next) => {
          if (next === FOLLOW) onChange(null);
          else if (next === CUSTOM) onChange('');
          else onChange(next);
        }}
      >
        <SelectTrigger>
          <SelectValue placeholder={inheritLabel} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={FOLLOW}>{inheritLabel}</SelectItem>
          {offered.map((model) => (
            <SelectItem key={model} value={model}>
              {model}
            </SelectItem>
          ))}
          <SelectItem value={CUSTOM}>{customLabel}</SelectItem>
        </SelectContent>
      </Select>
      {isCustom ? (
        <Input
          value={current}
          placeholder={customPlaceholder}
          aria-label={`${label}模型名称`}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : null}
      {hint ? <span className="text-[11px] text-muted-foreground">{hint}</span> : null}
    </div>
  );
}

/** `provider/model` -> `model`: the name a provider prefix decorates. */
export function bareName(value: string): string {
  return value.includes('/') ? (value.split('/').pop() ?? value) : value;
}

/** The value to persist for a chosen model: the engine's `provider/model` form. */
export function normalizeModel(value: string | null | undefined): string | null {
  return normalizeModelId(value) ?? null;
}