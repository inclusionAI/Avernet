import { Cpu } from 'lucide-react';
import type React from 'react';
import { ClaudeIcon, CodefuseIcon, OpenAIIcon, QoderIcon, QwenIcon } from './icons';

export type RuntimeIconComponent = React.ComponentType<{
  size?: number | string;
  className?: string;
}>;

export function getModelRuntime(model?: { runtime?: string | null }): string {
  return model?.runtime || '';
}

const names: Record<string, string> = {
  'claude-code': 'Claude Code',
  codex: 'Codex',
  'qwen-code': 'Qwen Code',
  'qwen-coder': 'Qwen Code',
  qoder: 'Qoder',
  'codefuse-antcc': 'Claude Code',
  'codefuse-codex': 'Codex',
};

const RUNTIME_ICON_MAP: Record<string, RuntimeIconComponent> = {
  'claude-code': ClaudeIcon,
  codex: OpenAIIcon,
  'qwen-code': QwenIcon,
  'qwen-coder': QwenIcon,
  qoder: QoderIcon,
  'codefuse-antcc': CodefuseIcon,
  'codefuse-codex': CodefuseIcon,
};

export function getRuntimeIcon(runtime?: string): RuntimeIconComponent {
  return (runtime && RUNTIME_ICON_MAP[runtime]) || Cpu;
}

export function getCodingRuntimeGroupKey(runtime?: string): string {
  return runtime === 'claude-code' || runtime === 'codefuse-antcc'
    ? 'cc'
    : runtime === 'codex' || runtime === 'codefuse-codex'
    ? 'codex'
    : runtime || '';
}

export function getCodingRuntimeGroupDisplayName(key?: string): string {
  return key === 'cc' ? 'Claude Code' : key === 'codex' ? 'Codex' : names[key || ''] || key || '';
}

export function getCodingRuntimeGroupIcon(key?: string): RuntimeIconComponent {
  if (key === 'cc') return getRuntimeIcon('claude-code');
  if (key === 'codex') return getRuntimeIcon('codex');
  return getRuntimeIcon(key);
}

export function getCodingRuntimeSourceLabel(runtime?: string): string {
  return runtime?.startsWith('codefuse-') ? 'CodeFuse' : '';
}

export function isCodefuseRuntime(runtime?: string): boolean {
  return runtime === 'codefuse-antcc' || runtime === 'codefuse-codex';
}
