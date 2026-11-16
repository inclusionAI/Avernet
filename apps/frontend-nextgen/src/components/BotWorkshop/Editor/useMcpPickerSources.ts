// src/components/BotWorkshop/Editor/useMcpPickerSources.ts
//
// MCP 来源插槽消费 hook（CapabilityPickerModal 专用）：读取 getBotMcpPickerSources
// capability 清单，过滤来源选项、归一当前选中值、派生弹窗描述文案。

import { getCapabilities, type BotMcpPickerSource } from '@/capabilities';

/**
 * MCP 一级来源 Segmented 选项（label 与老平台一致）。
 * 可选清单由 `getBotMcpPickerSources` capability 决定（部署 overlay 可收窄，组件不得硬编码全量）。
 */
const MCP_PICKER_SOURCE_OPTIONS: { value: BotMcpPickerSource; label: string }[] = [
  { value: 'internal', label: '内部 MCP' },
  { value: 'open-platform', label: '开放平台' },
];

/**
 * MCP 一级来源选项按 capability 清单过滤（部署 overlay 可收窄，如仅内部）；
 * 当前选中值不在清单内（overlay 收敛/异常路径）时回落首个允许项，搜索只发允许来源。
 * 描述文案随来源数自适应：单来源不提「按来源分类」（弹窗描述与搜索占位符已交代范围）。
 */
export function useMcpPickerSources(selected: BotMcpPickerSource) {
  const mcpSources = getCapabilities().getBotMcpPickerSources().value;
  const mcpSourceOptions = MCP_PICKER_SOURCE_OPTIONS.filter((option) => mcpSources.includes(option.value));
  const activeMcpSource = mcpSourceOptions.some((option) => option.value === selected)
    ? selected
    : mcpSourceOptions[0]?.value ?? 'internal';
  const mcpDescription =
    mcpSourceOptions.length > 1
      ? '从 MCP 市场选择，可按来源分类和搜索后一次添加多个能力。'
      : '从 MCP 市场选择，可一次添加多个能力。';
  return { mcpSourceOptions, activeMcpSource, mcpDescription };
}
