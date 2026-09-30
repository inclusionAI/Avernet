/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Hosting24x7ConfigField - 7x24 小时托管配置字段。
 *
 * 抽离为独立组件后，应用 Coding 与 AgentCoding 模板 Bot 均可复用同一交互。
 */

import { cn } from '@/utils/cn';

export interface Hosting24x7ConfigFieldProps {
  label?: string;
  description?: string;
  value?: boolean;
  disabled?: boolean;
  onChange: (value: boolean) => void;
}

export function Hosting24x7ConfigField({
  label = '7x24 小时托管',
  description = '开启后 Bot 将自动监控并处理需求，无需人工干预',
  value = true,
  disabled = false,
  onChange,
}: Hosting24x7ConfigFieldProps) {
  return (
    <div className="space-y-1.5">
      <label
        className={cn(
          'flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-all',
          value ? 'bg-[#eff6ff]/70 border-[#bfdbfe]' : 'bg-background border-[#e2e8f0] hover:border-[#cbd5e1]',
          disabled && 'opacity-50 cursor-not-allowed',
        )}
      >
        <div className="flex items-center gap-2 mt-0.5">
          <input
            type="checkbox"
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
            disabled={disabled}
            className="h-4 w-4 cursor-pointer rounded border-[#cbd5e1] text-[#2563eb] focus:ring-[#3b82f6] disabled:opacity-50"
          />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold text-slate-600">{label}</div>
          {description && <p className="text-[11px] text-slate-500 mt-0.5">{description}</p>}
        </div>
      </label>
    </div>
  );
}

export default Hosting24x7ConfigField;
