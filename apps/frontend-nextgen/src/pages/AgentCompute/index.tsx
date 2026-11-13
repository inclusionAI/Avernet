import { useEffect, useMemo } from 'react';
import { AgentComputePanel } from '@/components/agentswarm/AgentComputePanel';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { getCapabilities } from '@/capabilities';

/**
 * Deep-link host for Agent Compute.
 *
 * The product entry is the chat composer's plus menu, which opens the same panel
 * beside a conversation. This route exists so a link to the surface still lands
 * somewhere: it opens the panel for the current human and renders it full width.
 * There is deliberately no second layout — the panel is the one implementation.
 */
export default function AgentComputePage() {
  const human = useMemo(() => {
    const resolved = getCapabilities().getHumanIdentity();
    return resolved.status === 'available' ? resolved.value : null;
  }, []);
  const userId = human?.userId ?? null;
  const open = useAgentComputePanelStore((state) => state.open);

  useEffect(() => {
    // A route visit has no conversation, so it is scoped to the identity's own
    // "session" — the panel still refuses to cross into a chat session.
    if (userId) open(userId);
  }, [userId, open]);

  return (
    <div className="relative flex h-full min-h-0 flex-col [&>aside]:static [&>aside]:w-full [&>aside]:max-w-none">
      <AgentComputePanel sessionId={userId ?? ''} userId={userId} enabled={Boolean(userId)} />
    </div>
  );
}