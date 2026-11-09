import { Boxes, History, Plus, X } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ComputeConfigForm } from '@/components/agentswarm/ComputeConfigForm';
import { PanelResizeHandle } from '@/components/agentswarm/PanelResizeHandle';
import { RunCard } from '@/components/agentswarm/RunCard';
import { RunDetailPanel } from '@/components/agentswarm/RunDetailPanel';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { useRunDetail } from '@/hooks/useRunDetail';
import { useRunList } from '@/hooks/useRunList';
import { useRunPolling } from '@/hooks/useRunPolling';
import { createAgentDraft, toSubmitDTO, type ComputeDraft, type DraftIssue } from '@/domain/agentswarm/draft';
import { toIdentityOptions } from '@/domain/agentswarm/identityOptions';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { explainSubmitRefusal } from '@/services/agentswarm/refusals';
import { submitRun } from '@/services/agentswarm/service';
import { resolveAgentswarmTenant } from '@/services/agentswarm/tenant';
import { isTerminalStatus } from '@/services/agentswarm/types';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { useRunGoalStore } from '@/stores/runGoalStore';
import { PANEL_MAX_WIDTH, PANEL_MIN_WIDTH } from '@/stores/agentComputePanelStore';
import { useMediaQuery } from '@/hooks/useMediaQuery';

export interface AgentComputePanelProps {
  /** The conversation the panel was opened for. */
  sessionId: string;
  /**
   * The authenticated user whose Bots may serve as identity sources.
   *
   * Passed in rather than read from `identityStore`: that store is populated by
   * the bot-workshop editor, not by chat, so reading it here yielded an empty
   * list and a picker with nothing to choose.
   */
  userId?: string | null;
  /**
   * The Bot this conversation is with, used to pre-fill the identity source.
   * The run then inherits the identity the user is already talking to.
   */
  defaultIdentityBotId?: string | null;
  enabled?: boolean;
}

/**
 * The in-conversation Agent Compute panel.
 *
 * Renders as a layout sibling of the conversation rather than a route, so the
 * chat stays visible while a run is configured and observed. It is scoped to one
 * session: a panel opened elsewhere renders nothing here.
 */
export function AgentComputePanel({
  sessionId,
  userId,
  defaultIdentityBotId,
  enabled = true,
}: AgentComputePanelProps) {
  const store = useAgentComputePanelStore();
  const panelWidth = store.width;
  const setPanelWidth = store.setWidth;
  // Below xl the panel overlays the conversation and its width is the whole
  // viewport, so a resize affordance there would do nothing.
  const wide = useMediaQuery('(min-width: 1280px)');
  const sessionMatch = store.sessionId === sessionId;
  const runId = sessionMatch ? store.runId : undefined;
  const view = sessionMatch ? store.view : 'draft';
  const tenant = useMemo(() => resolveAgentswarmTenant(), []);
  const activeIdentityId = userId ?? null;

  const emptyDraft = (): ComputeDraft => ({
    goal: '',
    agents: [createAgentDraft({ kind: 'bot' })],
    // Default to the Bot this conversation is with: the run then inherits the
    // identity the user is already talking to, instead of forcing a choice.
    identityBotId: defaultIdentityBotId ?? undefined,
  });
  const [draft, setDraft] = useState<ComputeDraft>(emptyDraft);
  const [submitting, setSubmitting] = useState(false);
  const [submitIssues, setSubmitIssues] = useState<DraftIssue[]>([]);

  const { chatBots, isLoading: botsLoading, error: botsError } = useOwnedBots(
    activeIdentityId ?? null,
    Boolean(activeIdentityId),
  );
  const identityOptions = useMemo(() => toIdentityOptions(chatBots), [chatBots]);

  // The chat's Bot can resolve after the panel mounts, and a draft is built once.
  // Fill the identity in then, but never overwrite a choice the user has made.
  useEffect(() => {
    if (!defaultIdentityBotId) return;
    setDraft((current) =>
      current.identityBotId ? current : { ...current, identityBotId: defaultIdentityBotId },
    );
  }, [defaultIdentityBotId]);

  const open = Boolean(enabled && store.sessionId);
  const list = useRunList({ tenant, enabled: open });
  const polling = useRunPolling({ tenant, runId, enabled: open });
  const summary = polling.run ?? list.runs.find((run) => run.run_id === runId) ?? null;

  // A just-submitted run is polled before the refreshed list contains it. Show it
  // at the top so the user sees the task they created rather than waiting for a
  // list round trip.
  const rows = useMemo(() => {
    if (!summary || list.runs.some((run) => run.run_id === summary.run_id)) return list.runs;
    return [summary, ...list.runs];
  }, [list.runs, summary]);
  const terminal = Boolean(summary && isTerminalStatus(summary.status));
  // The detail is read whenever a run is selected, since that selection is what
  // expands it in the list.
  const detail = useRunDetail({ tenant, runId, opened: Boolean(runId), terminal });
  const rememberGoal = useRunGoalStore((state) => state.remember);

  // The list carries a goal prefix; the open run's detail fills in the full text.
  useEffect(() => {
    if (runId && detail.detail?.goal) rememberGoal(runId, detail.detail.goal);
  }, [runId, detail.detail?.goal, rememberGoal]);

  const selectRun = useCallback(
    (nextRunId: string) => {
      // 再次点击已展开的运行就收起它，与列表行的折叠语义一致。
      if (nextRunId === runId) store.clearRun();
      else store.selectRun(nextRunId);
    },
    [store, runId],
  );

  if (!enabled || !sessionMatch) return null;

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitIssues([]);
    try {
      const result = await submitRun(tenant, toSubmitDTO(draft, tenant));
      notifySuccess(`计算任务已提交：${result.run_id}`);
      // The list has no goal, so remember the one this submit used.
      rememberGoal(result.run_id, draft.goal);
      store.selectRun(result.run_id);
      list.refresh();
    } catch (reason) {
      const refusal = explainSubmitRefusal(reason);
      // A gateway refusal is not a field error: naming a field would point the
      // user at a control that is already valid. The banner owns it.
      setSubmitIssues([{ field: 'submit', message: refusal.message }]);
      notifyError(refusal.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <aside
      role="region"
      aria-label="计算任务副屏"
      style={{ width: wide ? panelWidth : undefined }}
      className="absolute inset-0 z-30 flex min-h-0 min-w-0 flex-col border-l border-border bg-card text-card-foreground xl:relative xl:inset-auto xl:shrink-0"
    >
      {/* 只有并排（xl+）时才可拖：窄屏副屏是覆盖层，宽度就是整个可用区。 */}
      <PanelResizeHandle
        width={panelWidth}
        onWidthChange={setPanelWidth}
        min={PANEL_MIN_WIDTH}
        max={PANEL_MAX_WIDTH}
        label="调整计算任务副屏宽度"
      />
      <header className="flex shrink-0 flex-col gap-3 border-b border-border px-5 py-4">
        {/* 标题与关闭一行，操作单独一行：副屏只有 440px，挤在一行会把
            标题说明压成两三行窄条。 */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 text-base font-semibold">
              <Boxes className="h-4 w-4 shrink-0 text-primary" />
              <span className="truncate">{view === 'draft' ? '计算任务' : '计算任务运行详情'}</span>
            </h2>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {view === 'draft'
                ? '根据工作目标定义执行 Agent，启动多个容器协同批量计算。'
                : '查看每次运行的状态、执行 Agent 与最终结果。'}
            </p>
          </div>
          <Button
            size="icon"
            variant="ghost"
            className="shrink-0"
            aria-label="收起计算任务副屏"
            onClick={() => store.close()}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant={view === 'draft' ? 'default' : 'outline'}
            onClick={() => {
              // 新建任务：清空草稿再回到配置视图，否则会接着上一次的内容继续。
              setDraft(emptyDraft());
              setSubmitIssues([]);
              store.showDraft(sessionId);
            }}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            新建任务
          </Button>
          <Button
            size="sm"
            variant={view === 'detail' ? 'default' : 'outline'}
            onClick={() => store.showDetail(sessionId, runId ?? list.runs[0]?.run_id)}
          >
            <History className="mr-1 h-3.5 w-3.5" />
            运行详情
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {view === 'draft' ? (
          <div className="min-h-0 flex-1 overflow-auto">
            <ComputeConfigForm
              draft={draft}
              onChange={setDraft}
              identityOptions={identityOptions}
              identityLoading={botsLoading}
              identityError={botsError}
              submitting={submitting}
              submitIssues={submitIssues}
              onSubmit={handleSubmit}
            />
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto p-3">
            <span className="text-xs font-medium text-foreground">运行记录</span>

            {list.error ? (
              <p className="text-xs text-destructive">{list.error}</p>
            ) : !list.loaded ? (
              <p className="text-xs text-muted-foreground">正在读取运行记录…</p>
            ) : rows.length === 0 ? (
              <Empty description="当前租户还没有运行记录。" />
            ) : (
              <div className="flex flex-col gap-2">
                {rows.map((run) => (
                  <div key={run.run_id} className="flex flex-col gap-2">
                    <RunCard run={run} selected={run.run_id === runId} onSelect={selectRun} />
                    {/* 选中即展开：这次运行的操作与结果就挂在它自己下面，
                        而不是另起一个底部操作区。 */}
                    {run.run_id === runId ? (
                      // 详情是从属内容：缩进 + 左侧强调线，与列表卡片区分开。
                      <div className="ml-3 border-l-2 border-primary/40 pl-2">
                        <RunDetailPanel
                          tenant={tenant}
                          run={run}
                          goal={detail.detail?.goal}
                          detail={detail.detail}
                          loading={detail.phase === 'loading'}
                          error={detail.error}
                          onReload={detail.reload}
                          onCancelled={() => {
                            polling.refresh();
                            list.refresh();
                          }}
                        />
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
            {polling.error ? <p className="text-xs text-destructive">{polling.error}</p> : null}
          </div>
        )}
      </div>
    </aside>
  );
}