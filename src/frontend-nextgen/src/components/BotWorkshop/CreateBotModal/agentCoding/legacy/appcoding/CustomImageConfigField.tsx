import { getCapabilities } from '@/capabilities';
import {
  CreateBotTooltip,
  CreateBotTooltipContent,
  CreateBotTooltipProvider,
  CreateBotTooltipTrigger,
} from '@/components/BotWorkshop/CreateBotModal/CreateBotTooltip';
import { cn } from '@/utils/cn';
import { HelpCircle, Wrench } from 'lucide-react';
import React from 'react';

export interface CustomImageConfigFieldProps {
  value?: string;
  onChange: (image: string) => void;
  disabled?: boolean;
  label?: string;
  required?: boolean;
  placeholder?: string;
  description?: string;
  className?: string;
}

export const CustomImageConfigField: React.FC<CustomImageConfigFieldProps> = ({
  value = '',
  onChange,
  disabled = false,
  label = '镜像地址',
  required = false,
  placeholder = '输入镜像地址，如 registry/repo/image:tag',
  description,
  className,
}) => {
  const resources = getCapabilities().getAgentCodingInternalResources().value;
  return (
    <div className={cn('space-y-1.5', className)}>
      <label className="text-xs font-semibold text-slate-600 flex items-center gap-1">
        {label}
        {required ? (
          <span className="text-red-500 ml-0.5">*</span>
        ) : (
          <span className="text-slate-400 font-normal">（可选）</span>
        )}
        <CreateBotTooltipProvider delayDuration={200}>
          <CreateBotTooltip>
            <CreateBotTooltipTrigger asChild>
              <HelpCircle className="w-3.5 h-3.5 text-slate-400 cursor-help" />
            </CreateBotTooltipTrigger>
            <CreateBotTooltipContent side="top">
              <p className="max-w-[220px]">
                查看
                {resources.imageManualUrl ? (
                  <a
                    href={resources.imageManualUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-500 hover:text-blue-600 hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    镜像制作流程
                  </a>
                ) : null}
              </p>
            </CreateBotTooltipContent>
          </CreateBotTooltip>
        </CreateBotTooltipProvider>
      </label>
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          className="flex-1 min-w-0 rounded-lg border border-slate-200 bg-background px-3 py-1.5 text-sm focus:border-transparent focus:outline-none focus:ring-1 focus:ring-slate-300 placeholder:text-slate-300 disabled:bg-muted disabled:text-slate-400"
        />
        {resources.imageBuildUrl ? (
          <CreateBotTooltipProvider delayDuration={200}>
            <CreateBotTooltip>
              <CreateBotTooltipTrigger asChild>
                <a
                  href={resources.imageBuildUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 flex items-center justify-center w-8 h-8 rounded-lg border border-slate-200 text-slate-500 hover:text-lavender-600 hover:border-lavender-300 transition-colors"
                >
                  <Wrench className="w-4 h-4" />
                </a>
              </CreateBotTooltipTrigger>
              <CreateBotTooltipContent side="top">
                <p>前往制作镜像</p>
              </CreateBotTooltipContent>
            </CreateBotTooltip>
          </CreateBotTooltipProvider>
        ) : null}
      </div>
      {description && <p className="text-[11px] leading-relaxed text-slate-400">{description}</p>}
    </div>
  );
};

export default CustomImageConfigField;
