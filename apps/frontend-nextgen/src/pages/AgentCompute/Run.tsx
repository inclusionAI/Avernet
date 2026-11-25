import { PageHeader } from '@/components/Common/PageHeader';
import { AgentComputePanel } from '@/components/agentswarm/AgentComputePanel';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { getCapabilities } from '@/capabilities';
import { history, useParams } from '@umijs/max';
import { useEffect } from 'react';

const BASE = '/agent-compute';

/**
 * Deep link to one run.
 *
 * The run panel is the single implementation of the detail view (it already
 * renders the transcript, agent breakdown and results), so this route opens it
 * scoped to the link's run instead of forking a second layout.
 *
 * Identity is read synchronously from the stores by `getHumanIdentity`, so a
 * direct visit must subscribe to them: a snapshot taken on first render is
 * often still empty while the stores hydrate, and caching it leaves the page
 * permanently blank.
 */
export default function AgentComputeRunPage() {
  const { runId = '' } = useParams<{ runId: string }>();

  useExternalAuthStore((state) => state.user);
  useWorkspaceStore((state) => state.identities);

  const resolved = getCapabilities().getHumanIdentity();
  const userId = resolved.status === 'available' ? resolved.value?.userId ?? null : null;
  const open = useAgentComputePanelStore((state) => state.open);
  const selectRun = useAgentComputePanelStore((state) => state.selectRun);

  useEffect(() => {
    // A route visit has no conversation, so it is scoped to the identity's own
    // "session" — the panel still refuses to cross into a chat session.
    if (userId) open(userId);
  }, [userId, open]);

  useEffect(() => {
    if (userId && runId) selectRun(runId);
  }, [userId, runId, selectRun]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="px-5 pt-6">
        <PageHeader title="计算任务详情" onBack={() => history.push(BASE)} />
      </div>
      <div className="relative flex min-h-0 flex-1 flex-col [&>aside]:static [&>aside]:w-full [&>aside]:max-w-none">
        {userId ? (
          <AgentComputePanel sessionId={userId} userId={userId} enabled />
        ) : (
          <p role="status" className="p-6 text-sm text-muted-foreground">
            正在获取登录身份…若长时间无响应，请先登录后再访问本页。
          </p>
        )}
      </div>
    </div>
  );
}