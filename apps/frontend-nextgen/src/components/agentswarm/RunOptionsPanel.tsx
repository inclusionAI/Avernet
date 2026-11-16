import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Switch } from '@/components/ui/Switch';
import { ModelSelector } from './ModelSelector';
import { OPTION_FIELDS, validateOptions } from '@/domain/agentswarm/options';
import type { RunOptionsDTO } from '@/services/agentswarm/types';

export interface RunOptionsPanelProps {
  /** Run-level `options`; includes `model_name`, the default for agents. */
  options: RunOptionsDTO | null | undefined;
  onChange: (options: RunOptionsDTO) => void;
}

/**
 * Preset values used when the user first turns on custom limits.
 *
 * These are starting points, not deployment defaults: the gateway applies its own
 * defaults for anything omitted, and these are only what the form seeds.
 */
const PRESETS: Record<string, number> = {
  max_total_agents: 32,
  max_width: 3,
  // The turn budget is half the UI ceiling: the gateway accepts more, but this
  // surface has no human-authorization parks, which is what a very large budget
  // exists for.
  max_turns: 64,
  max_attempts: 3,
  // Both timeouts default to the gateway's own ceiling: provisioning slow
  // environments is the usual reason to touch them, and narrowing is the
  // caller's job from there.
  setup_timeout_seconds: 1800,
  per_attempt_timeout_seconds: 1800,
  slice_max_seconds: 3600,
};

const LIMIT_KEYS = OPTION_FIELDS.map((field) => field.key);

const asNumber = (raw: string): number | null => {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
};

/** Whether the user has overridden any platform limit (as opposed to the model). */
export function hasCustomLimits(options: RunOptionsDTO | null | undefined): boolean {
  if (!options) return false;
  return LIMIT_KEYS.some((key) => {
    const value = options[key];
    return value !== undefined && value !== null;
  });
}

/**
 * Execution limits and the run-level default model.
 *
 * Follows the PRD: a switch turns custom limits on, the fields read as presets
 * while it is on, and turning it off clears them so the run falls back to the
 * deployment's own bounds. The model sits outside the switch because choosing a
 * model is not a limit override.
 */
export function RunOptionsPanel({ options, onChange }: RunOptionsPanelProps) {
  const custom = hasCustomLimits(options);
  const issues = validateOptions(options);
  const issueFor = (key: keyof RunOptionsDTO) => issues.find((issue) => issue.key === key)?.message;
  const set = (patch: RunOptionsDTO) => onChange({ ...(options ?? {}), ...patch });

  const toggleCustom = (next: boolean) => {
    if (next) {
      onChange({ ...(options ?? {}), ...PRESETS });
      return;
    }
    // Clearing the limits must not discard the model selection.
    const { model_name } = options ?? {};
    onChange(model_name ? { model_name } : {});
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Label htmlFor="compute-limits">自定义执行限制</Label>
        <Switch id="compute-limits" checked={custom} onCheckedChange={toggleCustom} />
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        默认由平台控制。开启后按下方数值提交，留空则沿用平台默认值。
      </p>

      {custom ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {OPTION_FIELDS.map((field) => {
            const raw = options?.[field.key];
            const message = issueFor(field.key);
            return (
              <div key={field.key} className="space-y-1.5">
                <Label htmlFor={`compute-${field.key}`}>
                  {field.label}
                  <span className="ml-1 font-normal text-muted-foreground">
                    （{field.min}–{field.max}）
                  </span>
                </Label>
                <Input
                  id={`compute-${field.key}`}
                  type="number"
                  min={field.min}
                  max={field.max}
                  step={field.integer ? 1 : 'any'}
                  className="h-8 text-xs"
                  value={raw === undefined || raw === null ? '' : String(raw)}
                  aria-invalid={Boolean(message) || undefined}
                  onChange={(event) =>
                    set({ [field.key]: asNumber(event.target.value) } as RunOptionsDTO)
                  }
                />
                {message ? (
                  <p role="alert" className="text-xs text-destructive">
                    {message}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      <ModelSelector
        label="运行默认模型"
        inheritLabel="使用平台默认模型"
        hint="未单独指定模型的 Agent 使用该模型。"
        value={options?.model_name ?? null}
        onChange={(value) => set({ model_name: value })}
      />
    </div>
  );
}