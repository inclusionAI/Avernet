import { getCapabilities } from '@/capabilities';
import type { BotEditorPanelExtension } from '@/capabilities/types';
import { createElement, useMemo } from 'react';
const reserved = new Set(['capability', 'resource', 'routine', 'escort', 'hosting']);
export function validEditorPanels(panels: BotEditorPanelExtension[]): BotEditorPanelExtension[] {
  const seen = new Set<string>();
  return panels.filter((panel) => {
    if (!/^[a-z][a-z0-9-]*$/.test(panel.id) || reserved.has(panel.id) || seen.has(panel.id)) return false;
    seen.add(panel.id);
    return true;
  });
}
export function useBotEditorPanels() {
  const capabilities = getCapabilities();
  return useMemo(() => {
    const result = capabilities.getBotEditorPanels?.();
    const panels = result?.status === 'available' ? validEditorPanels(result.value) : [];
    return {
      panels,
      additionalTabs: panels.map((panel) => ({
        key: `extension:${panel.id}` as const,
        label: panel.label,
        icon: createElement(panel.Icon, { className: 'size-3.5 shrink-0' }),
      })),
    };
  }, [capabilities]);
}
