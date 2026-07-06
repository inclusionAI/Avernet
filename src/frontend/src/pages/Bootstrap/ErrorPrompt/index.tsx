import Button from '@/components/Button';
import { AlertCircle, Check, Copy, X } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  getErrorPromptState,
  hideErrorPrompt,
  subscribeErrorPrompt,
  type ErrorPromptStep,
} from './errorPromptState';

interface ErrorPromptProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  dismissible?: boolean;
}

function CopyableChip({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success('已复制');
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      console.error('[ErrorPrompt] copy failed:', err);
      toast.error('复制失败，请手动选择文本复制');
    }
  };
  return (
    <div className="mt-1.5 flex items-stretch gap-1.5 rounded-lg border border-slate-200 bg-slate-50 overflow-hidden">
      <code className="flex-1 min-w-0 px-2.5 py-1.5 text-[11px] font-mono text-slate-700 break-all leading-relaxed select-all">
        {value}
      </code>
      <button
        type="button"
        onClick={handleCopy}
        className="flex-shrink-0 px-2.5 flex items-center justify-center text-slate-500 hover:text-lavender-600 hover:bg-white border-l border-slate-200 transition-colors"
        aria-label="复制"
      >
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
    </div>
  );
}

function StepList({ steps }: { steps: ErrorPromptStep[] }) {
  return (
    <ol className="space-y-2.5">
      {steps.map((step, idx) => (
        <li key={idx} className="flex gap-2.5">
          <span className="flex-shrink-0 mt-0.5 w-5 h-5 rounded-full bg-lavender-100 text-lavender-700 text-[11px] font-semibold flex items-center justify-center">
            {idx + 1}
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-xs text-slate-700 leading-relaxed">
              {step.text}
            </p>
            {step.copyable && <CopyableChip value={step.copyable} />}
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function ErrorPrompt(props?: ErrorPromptProps) {
  const [state, setState] = useState(getErrorPromptState());

  useEffect(() => {
    return subscribeErrorPrompt(() => {
      const newState = getErrorPromptState();
      setState(newState);
    });
  }, []);

  const isPropsMode = props !== undefined && (props.title || props.message);

  const title = isPropsMode ? props?.title || '系统错误' : state.title;
  const message = isPropsMode ? props?.message || '' : state.message;
  const steps = isPropsMode ? undefined : state.steps;
  const description = isPropsMode ? undefined : state.description;
  const onRetry = isPropsMode ? props?.onRetry : state.onRetry;
  const dismissible = isPropsMode
    ? props?.dismissible ?? false
    : state.dismissible;
  const visible = isPropsMode ? true : state.visible;

  if (!visible) return null;

  const hasBody = !!(steps?.length || description || message);

  return (
    <div
      data-global-error-prompt
      className="fixed inset-0 z-[10009] flex items-center justify-center bg-black/25 backdrop-blur-sm pointer-events-auto"
    >
      <div className="bg-white rounded-2xl shadow-xl border border-slate-200/80 w-[440px] max-w-[90vw] overflow-hidden">
        {/* 顶部色条 */}
        <div className="h-1 bg-red-500 rounded-t-2xl" />

        {/* Header */}
        <div className="flex items-center gap-3 px-6 pt-5 pb-4">
          <div className="w-9 h-9 rounded-xl bg-red-50 flex items-center justify-center flex-shrink-0">
            <AlertCircle size={18} className="text-red-500" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              请稍后重试或联系管理员
            </p>
          </div>
          {dismissible && (
            <button
              type="button"
              onClick={hideErrorPrompt}
              className="w-7 h-7 rounded-lg flex items-center justify-center text-slate-400 hover:text-slate-600 hover:bg-slate-100 transition-colors flex-shrink-0"
            >
              <X size={15} />
            </button>
          )}
        </div>

        {/* Body */}
        {hasBody && (
          <div className="px-6 pb-4 space-y-3">
            {description && (
              <p className="text-xs text-slate-500 leading-relaxed">
                {description}
              </p>
            )}
            {steps && steps.length > 0 ? (
              <StepList steps={steps} />
            ) : (
              message && (
                <p className="text-sm text-slate-500 leading-relaxed whitespace-pre-line">
                  {message}
                </p>
              )
            )}
          </div>
        )}

        {/* Actions */}
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-slate-100">
          {dismissible ? (
            <>
              <Button
                variant="default"
                ghost
                onClick={() => window.location.reload()}
              >
                刷新页面
              </Button>
              <Button variant="danger" soft onClick={hideErrorPrompt}>
                我知道了
              </Button>
            </>
          ) : (
            <>
              <Button
                variant="default"
                ghost
                onClick={() => window.location.reload()}
              >
                刷新页面
              </Button>
              {onRetry && (
                <Button variant="danger" soft onClick={onRetry}>
                  重试
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
