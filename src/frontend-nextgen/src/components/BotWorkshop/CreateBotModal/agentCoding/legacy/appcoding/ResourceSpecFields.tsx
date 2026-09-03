import { Input } from '@/components/ui/Input';
import { isPositiveIntegerInput } from './AppCodingConfigUtils';
interface ResourceSpecFieldsProps {
  enabled: boolean;
  disabled: boolean;
  cpu: string;
  memory: string;
  disk: string;
  setCpu: (value: string) => void;
  setMemory: (value: string) => void;
  setDisk: (value: string) => void;
}
export function ResourceSpecFields({
  enabled,
  disabled,
  cpu,
  memory,
  disk,
  setCpu,
  setMemory,
  setDisk,
}: ResourceSpecFieldsProps) {
  if (!enabled) return null;
  const fields = [
    ['CPU（核）', cpu, setCpu],
    ['Memory（GiB）', memory, setMemory],
    ['Disk（GiB）', disk, setDisk],
  ] as const;
  const filledCount = [cpu, memory, disk].filter((value) => value.trim()).length;
  const invalid = [cpu, memory, disk].some((value) => !isPositiveIntegerInput(value));
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-semibold text-slate-600">
        资源规格<span className="ml-1 font-normal text-slate-400">（可选，正整数；不填走平台默认，填则三项必填）</span>
      </label>
      <div className="grid grid-cols-3 gap-2">
        {fields.map(([label, value, setter]) => (
          <div key={label} className="space-y-1">
            <span className="text-[11px] text-slate-500">{label}</span>
            <Input
              type="text"
              inputMode="numeric"
              value={value}
              onChange={(event) => setter(event.target.value)}
              placeholder={label.startsWith('CPU') ? '如 2' : label.startsWith('Memory') ? '如 4' : '如 50'}
              disabled={disabled}
              className={
                invalid ? 'border-red-300 focus-visible:ring-red-400' : 'border-slate-200 focus-visible:ring-slate-300'
              }
            />
          </div>
        ))}
      </div>
      {invalid ? (
        <p className="text-[11px] text-red-500">CPU、Memory、Disk 只能填正整数（如 1、2、4），留空将走平台默认。</p>
      ) : filledCount > 0 && filledCount < 3 ? (
        <p className="text-[11px] text-red-500">填写资源规格时，CPU、Memory、Disk 三项必须全部填写。</p>
      ) : null}
    </div>
  );
}
