import { Input } from '@/components/ui/Input';
import { Textarea } from '@/components/ui/Textarea';
import { localDate, type CreateDraft } from '@/domain/evolution/createTask';
import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { Choice, Field, Section } from './shared';

export function DiagnosisSettings({
  draft,
  set,
  setDraft,
  rangeDays,
  setRangeDays,
  modelField,
}: {
  draft: CreateDraft;
  set: <K extends keyof CreateDraft>(key: K, value: CreateDraft[K]) => void;
  setDraft: Dispatch<SetStateAction<CreateDraft>>;
  rangeDays: string;
  setRangeDays: Dispatch<SetStateAction<string>>;
  modelField: (label: string) => ReactNode;
}) {
  return (
    <>
      <Section title="诊断范围" index={3}>
        <fieldset className="mb-5">
          <legend className="mb-2 text-[13px] font-normal leading-[22px]">
            <span aria-hidden className="mr-1 text-destructive">
              *
            </span>
            范围模式
          </legend>
          <div className="flex flex-wrap gap-4 text-[13px]">
            {[
              ['range', '按时间范围'],
              ['explicit', '按精确 Session'],
            ].map(([value, label]) => (
              <label key={value} className="flex items-center gap-2">
                <input
                  type="radio"
                  className="size-4 accent-primary"
                  name="session-range"
                  checked={draft.explicitSessions === (value === 'explicit')}
                  onChange={() => set('explicitSessions', value === 'explicit')}
                />
                {label}
              </label>
            ))}
          </div>
        </fieldset>
        {draft.explicitSessions ? (
          <div className="max-w-[490px]">
            <Field label="Session ID *">
              <Textarea
                required
                rows={4}
                placeholder="每行填写一个 Session ID，例如：session_id_001"
                title="每行一个，1到20个，重复标识会去重。"
                className="min-h-24"
                value={draft.sessionText}
                onChange={(e) => set('sessionText', e.target.value)}
              />
            </Field>
          </div>
        ) : (
          <div className="grid gap-x-5 gap-y-5 md:grid-cols-2">
            <Field label="会话时间范围">
              <Choice
                label="会话时间范围"
                value={rangeDays}
                options={['7', '14', '30', '90'].map((value) => ({ value, label: `最近 ${value} 天` }))}
                onChange={(value) => {
                  setRangeDays(value);
                  const now = new Date();
                  const start = new Date(now);
                  start.setDate(start.getDate() - Number(value));
                  setDraft((d) => ({ ...d, startDate: localDate(start), endDate: localDate(now) }));
                }}
              />
            </Field>
            <Field label="最多诊断 Session 数量">
              <Input
                type="number"
                required
                min={1}
                max={1000}
                value={draft.maxSessions}
                onChange={(e) => set('maxSessions', Number(e.target.value))}
              />
            </Field>
          </div>
        )}
      </Section>
      <Section title="诊断设置" index={4}>
        <div className="grid gap-5 md:grid-cols-3">
          {modelField('模型')}

          <Field label="Bad Case 数量">
            <Input
              type="number"
              required
              disabled={draft.explicitSessions}
              title={draft.explicitSessions ? '仅按时间范围诊断时生效' : undefined}
              min={0}
              max={100}
              value={draft.badCases}
              onChange={(e) => set('badCases', Number(e.target.value))}
            />
          </Field>
          <Field label="Good Case 数量">
            <Input
              type="number"
              required
              disabled={draft.explicitSessions}
              title={draft.explicitSessions ? '仅按时间范围诊断时生效' : undefined}
              min={0}
              max={100}
              value={draft.goodCases}
              onChange={(e) => set('goodCases', Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="mt-5">
          <Field label="关注问题 *">
            <Textarea
              required
              maxLength={1000}
              rows={4}
              className="min-h-24"
              placeholder="请输入关注问题"
              value={draft.focus}
              onChange={(e) => set('focus', e.target.value)}
            />
          </Field>
        </div>
      </Section>
    </>
  );
}
