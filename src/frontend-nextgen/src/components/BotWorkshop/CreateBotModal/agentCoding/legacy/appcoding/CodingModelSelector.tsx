import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import { Check, ChevronDown, ChevronRight, X } from 'lucide-react';
import React from 'react';
import type { ModelOption } from './CodingModelConfigField';
import { isCodefuseAuthPlaceholder } from './compat/codefuse';
import {
  getCodingRuntimeGroupDisplayName,
  getCodingRuntimeGroupIcon,
  getCodingRuntimeSourceLabel,
  getModelRuntime,
  getRuntimeIcon,
} from './compat/runtime';
interface CodingModelSelectorProps {
  disabled: boolean;
  selectedModel: ModelOption | null;
  selectedRuntime: string;
  isResolvingInitialModel: boolean;
  showDropdown: boolean;
  setShowDropdown: (value: boolean) => void;
  groupedEntries: [string, ModelOption[]][];
  activeRuntimeGroup: string | null;
  setActiveRuntimeGroup: (value: string) => void;
  hasCodefuseAuthPlaceholder: boolean;
  botId?: string;
  onSelect: (model: ModelOption) => void;
  onClear: () => void;
  onAuthorize: () => void;
}
export function CodingModelSelector({
  disabled,
  selectedModel,
  selectedRuntime,
  isResolvingInitialModel,
  showDropdown,
  setShowDropdown,
  groupedEntries,
  activeRuntimeGroup,
  setActiveRuntimeGroup,
  hasCodefuseAuthPlaceholder,
  botId,
  onSelect,
  onClear,
  onAuthorize,
}: CodingModelSelectorProps) {
  const renderModelOption = (model: ModelOption) => {
    const selected = selectedModel?.id === model.id && getModelRuntime(selectedModel) === getModelRuntime(model);
    const runtime = getModelRuntime(model);
    const RuntimeIcon = getRuntimeIcon(runtime);
    return (
      <Button
        key={`${runtime}:${model.id}`}
        type="button"
        variant="ghost"
        onClick={() => onSelect(model)}
        disabled={disabled}
        className={cn(
          'h-auto min-h-0 w-full cursor-pointer justify-start rounded-none border-0 border-t border-slate-50 bg-transparent px-3 py-2.5 text-left text-[13px] font-medium shadow-none transition-colors',
          selected ? 'bg-blue-50 text-blue-700' : 'text-slate-700 hover:bg-slate-50',
        )}
      >
        <RuntimeIcon size={14} className={selected ? 'text-blue-500' : 'text-slate-400'} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-[13px] !font-medium">
              {model.displayName || model.display_name || model.name || model.id}
            </span>
            {getCodingRuntimeSourceLabel(runtime) ? (
              <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
                {getCodingRuntimeSourceLabel(runtime)}
              </span>
            ) : null}
          </div>
          {model.description ? <p className="mt-0.5 truncate text-xs text-slate-400">{model.description}</p> : null}
        </div>
        {selected ? <Check size={14} className="shrink-0 text-blue-500" /> : null}
      </Button>
    );
  };
  const authPanel = (
    <div className="px-3 py-2">
      <Button
        type="button"
        variant="default"
        size="sm"
        className="justify-start border border-dashed border-input bg-muted font-medium text-muted-foreground shadow-none hover:border-[#bfdbfe] hover:bg-[#eff6ff]/70 hover:shadow-none"
        onClick={onAuthorize}
        disabled={!botId}
      >
        更多模型请授权 CodeFuse
      </Button>
    </div>
  );
  const providerOptions = (models: ModelOption[]) => (
    <>
      {Object.entries(
        models
          .filter((model) => !isCodefuseAuthPlaceholder(model))
          .reduce<Record<string, ModelOption[]>>((groups, model) => {
            const provider = model.provider || 'Other';
            (groups[provider] ||= []).push(model);
            return groups;
          }, {}),
      ).map(([provider, providerModels]) => (
        <div key={provider}>{providerModels.map(renderModelOption)}</div>
      ))}
      {models.some(isCodefuseAuthPlaceholder) ? authPanel : null}
    </>
  );
  return (
    <div className="relative">
      <Button
        type="button"
        variant="ghost"
        onClick={() => !disabled && setShowDropdown(!showDropdown)}
        disabled={disabled}
        className={cn(
          'flex h-auto min-h-0 w-full cursor-pointer items-center justify-between rounded-lg border border-input bg-background px-3 py-2 text-sm font-normal shadow-none transition-colors',
          disabled
            ? 'cursor-not-allowed bg-slate-50 text-slate-400'
            : 'hover:border-[#bfdbfe] focus:outline-none focus:ring-1 focus:ring-[#3b82f6]',
        )}
      >
        <span className="flex min-w-0 items-center gap-2">
          {React.createElement(getRuntimeIcon(selectedModel ? selectedRuntime : ''), {
            size: 14,
            className: selectedModel ? 'text-blue-500' : 'text-slate-400',
          })}
          {selectedModel ? (
            <span className="truncate">
              {selectedModel.displayName || selectedModel.display_name || selectedModel.name || selectedModel.id}
            </span>
          ) : isResolvingInitialModel ? (
            <span className="text-slate-400">模型列表加载中...</span>
          ) : (
            <span className="text-slate-400">请选择默认模型</span>
          )}
        </span>
        {selectedModel && !disabled ? (
          <span
            role="button"
            tabIndex={0}
            onClick={(event) => {
              event.stopPropagation();
              onClear();
            }}
            onKeyDown={(event) => {
              if (event.key !== 'Enter' && event.key !== ' ') return;
              event.preventDefault();
              event.stopPropagation();
              onClear();
            }}
            className="flex-shrink-0 rounded p-0.5 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
            aria-label="清空选择"
          >
            <X size={12} />
          </span>
        ) : (
          <ChevronDown className={cn('h-4 w-4 text-slate-400 transition-transform', showDropdown && 'rotate-180')} />
        )}
      </Button>
      {showDropdown && !disabled ? (
        <div className="absolute z-50 mt-1 max-h-80 w-full overflow-hidden rounded-lg border border-input bg-background shadow-lg">
          {groupedEntries.length > 0 ? (
            <div className="flex max-h-80 min-h-[120px] overflow-hidden">
              <div className="min-h-0 w-44 shrink-0 max-h-80 overflow-y-auto overscroll-contain border-r border-slate-100 bg-slate-50/70">
                {groupedEntries.map(([runtime, runtimeModels]) => {
                  const active = activeRuntimeGroup === runtime;
                  const count = runtimeModels.filter((model) => !!model.id).length;
                  const Icon = getCodingRuntimeGroupIcon(runtime);
                  return (
                    <Button
                      key={runtime}
                      type="button"
                      variant="ghost"
                      onMouseEnter={() => setActiveRuntimeGroup(runtime)}
                      onClick={() => setActiveRuntimeGroup(runtime)}
                      className={cn(
                        'h-auto min-h-0 w-full cursor-pointer justify-start rounded-none border-0 px-2.5 py-2 text-left text-xs font-semibold shadow-none transition-colors',
                        active
                          ? 'bg-white text-blue-600 !font-semibold shadow-[0_1px_2px_rgba(15,23,42,0.04)] hover:bg-white hover:text-blue-600'
                          : 'text-slate-600 hover:bg-white/70 hover:text-slate-600',
                      )}
                    >
                      <Icon size={13} className={active ? 'text-blue-500' : 'text-slate-400'} />
                      <span className="flex-1 whitespace-nowrap">{getCodingRuntimeGroupDisplayName(runtime)}</span>
                      <span className="text-[10px] text-slate-400">{count || '需授权'}</span>
                      <ChevronRight className="h-3 w-3 text-slate-300" />
                    </Button>
                  );
                })}
              </div>
              <div className="min-h-0 min-w-0 flex-1 max-h-80 overflow-y-auto overscroll-contain">
                {providerOptions(
                  groupedEntries.find(([runtime]) => runtime === activeRuntimeGroup)?.[1] ||
                    groupedEntries[0]?.[1] ||
                    [],
                )}
              </div>
            </div>
          ) : hasCodefuseAuthPlaceholder ? (
            authPanel
          ) : (
            <div className="px-3 py-6 text-center text-sm text-slate-400">暂无可用模型</div>
          )}
        </div>
      ) : null}
    </div>
  );
}
