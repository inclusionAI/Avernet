import { registerBuiltinSidePanels } from '@/assets/BcsWorkflowPanel';
import { registerTaskPanel } from '@/assets/TaskPanel';
import { defaultCapabilities } from '@/capabilities';
import { registerUmdPanelHandler } from '@/services/bcs/UmdPanel';
import { ensureReactGlobal } from '@/services/workspace';
import '@/services/workspace/chatBridge';

/** Assemble the side-panel capabilities available in Open Core. */
export function registerSidePanelWiring(): void {
  // tc-chat 2.0.0 no longer exports configureSidePanel; its own defaults apply.
  ensureReactGlobal();
  registerUmdPanelHandler();
  registerBuiltinSidePanels();
  registerTaskPanel();
}

export const appExtension = {
  capabilities: defaultCapabilities,
};
