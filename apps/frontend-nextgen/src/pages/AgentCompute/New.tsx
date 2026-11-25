import { PageHeader } from '@/components/Common/PageHeader';
import { ComputeConfigForm } from '@/components/agentswarm/ComputeConfigForm';
import { createAgentDraft, toSubmitDTO, type ComputeDraft, type DraftIssue } from '@/domain/agentswarm/draft';
import { toIdentityOptions } from '@/domain/agentswarm/identityOptions';
import { explainSubmitRefusal } from '@/services/agentswarm/refusals';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { submitRun } from '@/services/agentswarm/service';
import { resolveAgentswarmTenant } from '@/services/agentswarm/tenant';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useRunGoalStore } from '@/stores/runGoalStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { getCapabilities } from '@/capabilities';
import { history } from '@umijs/max';
import { useMemo, useState } from 'react';

const BASE = '/agent-compute';

const emptyDraft = (): ComputeDraft => ({
  goal: '',
  agents: [createAgentDraft({ kind: 'bot' })],
  identityBotId: undefined,
});

/**
 * 新建计算任务（PRD v1.7 §1：列表 → 配置 → 运行详情）。
 *
 * The config form is the same component the in-chat panel uses; only the shell
 * differs — a full-width page with a back link to the list, instead of a side
 * drawer. Submitting lands on the run's detail route.
 */
export default function AgentComputeNewPage() {
  const tenant = useMemo(() => resolveAgentswarmTenant(), []);

  useExternalAuthStore((state) => state.user);
  useWorkspaceStore((state) => state.identities);

  const resolved = getCapabilities().getHumanIdentity();
  const userId = resolved.status === 'available' ? resolved.value?.userId ?? null : null;

  const [draft, setDraft] = useState<ComputeDraft>(emptyDraft);
  const [submitting, setSubmitting] = useState(false);
  const [issues, setIssues] = useState<DraftIssue[]>([]);

  const { chatBots, isLoading, error } = useOwnedBots(userId, Boolean(userId));
  const identityOptions = useMemo(() => toIdentityOptions(chatBots), [chatBots]);

  const rememberGoal = useRunGoalStore((state) => state.remember);
  const selectRun = useAgentComputePanelStore((state) => state.selectRun);

  const handleSubmit = async () => {
    setSubmitting(true);
    setIssues([]);
    try {
      const result = await submitRun(tenant, toSubmitDTO(draft, tenant));
      notifySuccess(`计算任务已提交：${result.run_id}`);
      rememberGoal(result.run_id, draft.goal);
      selectRun(result.run_id);
      history.push(`${BASE}/${encodeURIComponent(result.run_id)}`);
    } catch (reason) {
      const refusal = explainSubmitRefusal(reason);
      setIssues([{ field: 'submit', message: refusal.message }]);
      notifyError(refusal.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-full flex-col bg-background">
      <main className="app-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="flex w-full flex-col gap-5 px-5 py-6">
          <PageHeader
            title="新建计算任务"
            description="配置执行 Agent 后提交；任务提交后进入运行详情。"
            onBack={() => history.push(BASE)}
          />
          {userId ? (
            <ComputeConfigForm
              draft={draft}
              onChange={setDraft}
              identityOptions={identityOptions}
              identityLoading={isLoading}
              identityError={error}
              submitting={submitting}
              submitIssues={issues}
              onSubmit={() => void handleSubmit()}
            />
          ) : (
            <p role="status" className="text-sm text-muted-foreground">
              正在获取登录身份…若长时间无响应，请先登录后再访问本页。
            </p>
          )}
        </div>
      </main>
    </div>
  );
}