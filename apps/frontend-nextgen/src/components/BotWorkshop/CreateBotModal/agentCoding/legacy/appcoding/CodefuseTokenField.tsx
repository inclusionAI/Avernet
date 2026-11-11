import { getCapabilities } from '@/capabilities';
import {
  CreateBotTooltip,
  CreateBotTooltipContent,
  CreateBotTooltipProvider,
  CreateBotTooltipTrigger,
} from '@/components/BotWorkshop/CreateBotModal/CreateBotTooltip';
import { cn } from '@/utils/cn';
import { AlertTriangle, HelpCircle, KeyRound } from 'lucide-react';
import React from 'react';

interface CodefuseTokenFieldProps {
  value: string;
  disabled?: boolean;
  validationError?: string | null;
  onChange: (value: string) => void;
}

export const CodefuseTokenField: React.FC<CodefuseTokenFieldProps> = ({
  value,
  disabled,
  validationError,
  onChange,
}) => {
  const codefuseTokenUrl = getCapabilities().getAgentCodingInternalResources().value.codefuseTokenUrl;
  return (
    <div className="space-y-1.5">
      <label className="flex items-center gap-1 text-xs font-semibold text-slate-600">
        <KeyRound size={12} className="text-[#2563eb]" />
        授权 Token
        <span className="ml-0.5 text-red-500">*</span>
        <CreateBotTooltipProvider delayDuration={200}>
          <CreateBotTooltip>
            <CreateBotTooltipTrigger asChild>
              <HelpCircle className="h-3.5 w-3.5 cursor-help text-slate-400" />
            </CreateBotTooltipTrigger>
            <CreateBotTooltipContent side="top">
              <p className="max-w-[220px]">
                前往
                {codefuseTokenUrl ? (
                  <a
                    href={codefuseTokenUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="mx-0.5 text-blue-500 hover:text-blue-600 hover:underline"
                  >
                    CodeFuse 授权页面
                  </a>
                ) : null}
                获取 Token 后粘贴到此处
              </p>
            </CreateBotTooltipContent>
          </CreateBotTooltip>
        </CreateBotTooltipProvider>
      </label>
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="粘贴授权 Token"
        disabled={disabled}
        className={cn(
          'w-full rounded-lg border px-3 py-1.5 font-mono text-sm placeholder:text-slate-300 focus:border-transparent focus:outline-none focus:ring-1 disabled:bg-slate-50 disabled:text-slate-400',
          validationError ? 'border-amber-300 focus:ring-amber-200' : 'border-slate-200 focus:ring-slate-300',
        )}
      />
      {validationError ? (
        <div className="flex items-start gap-1.5 text-xs text-amber-600">
          <AlertTriangle className="mt-0.5 h-3 w-3 flex-shrink-0" />
          <span>{validationError}</span>
        </div>
      ) : null}
    </div>
  );
};
