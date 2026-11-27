import { PageHeader } from '@/components/Common/PageHeader';
import { ComputeConfigForm } from '@/components/agentswarm/ComputeConfigForm';
import {
  createNewAgentRef,
  findDefinitionCollisionsLive,
  referenceExistingDefinition,
  resolveRosterRefs,
  toSubmitDTO,
  type ComputeDraft,
  type DefinitionCollision,
  type DraftIssue,
} from '@/domain/agentswarm/draft';
import { Button } from '@/components/ui/Button';
import { defaultIdentityBotId, toIdentityOptions } from '@/domain/agentswarm/identityOptions';
import { normalizeModelId } from '@/domain/agentswarm/models';
import { explainSubmitRefusal } from '@/services/agentswarm/refusals';
import { useOwnedBots } from '@/pages/Workspace/hooks/useOwnedBots';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { createDefinition, listDefinitions, submitRun } from '@/services/agentswarm/service';
import { resolveAgentswarmTenant } from '@/services/agentswarm/tenant';
import { useAgentComputePanelStore } from '@/stores/agentComputePanelStore';
import { useExternalAuthStore } from '@/stores/externalAuthStore';
import { useDefinitions } from '@/hooks/useDefinitions';
import { useRunGoalStore } from '@/stores/runGoalStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { getCapabilities } from '@/capabilities';
import { history } from '@umijs/max';
import { useCallback, useEffect, useMemo, useState } from 'react';

const BASE = '/agent-compute';

const emptyDraft = (): ComputeDraft => ({
  goal: '',
  // One blank entry to start from; it is saved as a definition at submit.
  refs: [createNewAgentRef({ kind: 'bot' })],
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

  // Preselect an execution Bot once the list arrives, so a first submission does
  // not fail on a field the user never had a reason to think about. A choice the
  // user already made is never overwritten.
  useEffect(() => {
    if (isLoading || error) return;
    setDraft((current) => {
      if ((current.identityBotId || '').trim()) return current;
      const fallback = defaultIdentityBotId(identityOptions);
      return fallback ? { ...current, identityBotId: fallback } : current;
    });
  }, [identityOptions, isLoading, error]);

  const rememberGoal = useRunGoalStore((state) => state.remember);
  const selectRun = useAgentComputePanelStore((state) => state.selectRun);

  const { refresh: refreshDefinitions } = useDefinitions(tenant);
  const [collisions, setCollisions] = useState<DefinitionCollision[]>([]);

  /** Names the roster would create — what the gateway is asked about. */
  const typedNames = useMemo(
    () =>
      draft.refs
        .filter((ref) => ref.origin === 'new')
        .map((ref) => ref.body.name.trim())
        .filter(Boolean),
    [draft.refs],
  );

  /**
   * Ask the gateway whether any of those names is already taken.
   *
   * Runs when a name field loses focus, which is the moment the user has
   * finished typing one. A per-keystroke check would ask about half-typed names
   * and show a banner for something that was never going to be submitted; a
   * check only at submit told them after the whole form was filled.
   *
   * A failed lookup is silent here: submit re-checks, and a banner saying the
   * lookup failed would be noise beside a form the user is still filling.
   */
  /** The gateway's answer for one exact name; the filter it applies is a LIKE. */
  const lookupDefinitionByName = useCallback(
    async (name: string) => {
      const page = await listDefinitions(tenant, { name, page: 1, page_size: 1 });
      return (page.items ?? []).find((item) => item.name === name) ?? null;
    },
    [tenant],
  );

  const checkNameCollisions = useCallback(async () => {
    if (!typedNames.length) {
      setCollisions([]);
      return;
    }
    try {
      const found = await findDefinitionCollisionsLive(draft, lookupDefinitionByName);
      setCollisions(found);
    } catch {
      setCollisions([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typedNames.join('\u0000'), lookupDefinitionByName]);

  /**
   * Saves the roster's new entries, or reports names that are already taken.
   *
   * There is no overwrite path: a name the library already holds is either
   * referenced as-is or renamed. Rewriting a stored definition from a task form
   * would change what every other run resolves to, which is not a decision this
   * screen should make on the user's behalf.
   */
  const persistNewEntries = async () => {
    // Checked against the gateway, not just the page in hand. The library is
    // paged, so a definition past the first page was invisible here and the
    // submit went through only to come back 409 — the user then had to notice
    // and redo it as a reference by hand.
    const found = await findDefinitionCollisionsLive(draft, lookupDefinitionByName);
    if (found.length) {
      // The banner owns this message; pushing it into `issues` as well printed
      // the same sentence twice — once in the form footer, once in the banner.
      setCollisions(found);
      setIssues([]);
      return null;
    }
    setCollisions([]);
    return resolveRosterRefs(draft, async (entry, name) => {
      const saved = await createDefinition(tenant, {
        name,
        role: entry.body.role,
        instructions: entry.body.instructions ?? '',
        kind: entry.body.kind,
        model: normalizeModelId(entry.body.model) ?? null,
        manifest_document: entry.body.config_manifest ?? '',
      });
      return saved.definition_id;
    });
  };

  /** Saves, then runs the same submit path — used by the "update existing" action. */
  const submitWith = async (resolvedIds: Record<string, string>) => {
    const result = await submitRun(tenant, toSubmitDTO(draft, tenant, resolvedIds));
    notifySuccess(`计算任务已提交：${result.run_id}`);
    rememberGoal(result.run_id, draft.goal);
    selectRun(result.run_id);
    refreshDefinitions();
    history.push(`${BASE}/${encodeURIComponent(result.run_id)}`);
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setIssues([]);
    try {
      // Every roster entry is submitted as a definition ref, so a blank agent
      // written in the form is saved first. A failure here aborts the submit
      // rather than falling back to an inline roster: the run would otherwise
      // reference agents that do not exist in the library.
      const resolvedIds = await persistNewEntries();
      if (!resolvedIds) return;
      await submitWith(resolvedIds);
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
          {/* PRD §7: the page states where the user is in the two-stage flow. */}
          <ol
            aria-label="计算任务流程"
            className="m-0 flex list-none items-center gap-3 p-0 text-xs"
          >
            {(
              [
                { label: '配置任务', current: true },
                { label: '查看运行', current: false },
              ] as const
            ).map((stage, index) => (
              <li key={stage.label} className="flex items-center gap-3">
                {index > 0 ? (
                  <span aria-hidden className="text-muted-foreground">
                    →
                  </span>
                ) : null}
                <span
                  aria-current={stage.current ? 'step' : undefined}
                  className={stage.current ? 'font-medium text-foreground' : 'text-muted-foreground'}
                >
                  {stage.label}
                </span>
              </li>
            ))}
          </ol>
          {userId ? (
            <div className="flex flex-col gap-6">
              {/* The form owns the PRD §7 two-column layout, with 运行设置 in
                  its right column. */}
              <ComputeConfigForm
                draft={draft}
                onChange={setDraft}
                identityOptions={identityOptions}
                identityLoading={isLoading}
                identityError={error}
                submitting={submitting}
                submitIssues={issues}
                tenant={tenant}
                onManageDefinitions={() => history.push(`${BASE}/definitions`)}
                onNameBlur={() => void checkNameCollisions()}
                collisionNotice={
                  collisions.length ? (
                    <div role="alert" className="space-y-2">
                      <p className="m-0 text-sm">
                        定义库中已有同名定义：
                        {collisions.map((item) => `「${item.name}」`).join('、')}
                      </p>
                      <p className="m-0 text-xs leading-5 text-muted-foreground">
                        直接使用库里已有的定义即可，不必再新建一份；也可以改名后再提交。
                      </p>
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          size="sm"
                          onClick={() => {
                            setDraft((current) => referenceExistingDefinition(current, collisions));
                            setCollisions([]);
                            setIssues([]);
                          }}
                        >
                          使用已有定义
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => setCollisions([])}>
                          我知道了，去改名
                        </Button>
                      </div>
                    </div>
                  ) : null
                }
                onSubmit={() => void handleSubmit()}
              />
            </div>
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