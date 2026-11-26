import { GenomeBaselinePanel } from '@/components/rsi/GenomeBaselinePanel';
import { RsiReviewQueue, RsiRunList } from '@/components/rsi/RsiRunNavigation';
import { RsiRunDetails } from '@/components/rsi/RsiRunDetails';
import { Button } from '@/components/ui/Button';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import { rsiDomainCode, rsiErrorMessage } from '@/services/rsi/errors';
import {
  getRsiBindings,
  getRsiCandidates,
  getRsiEvidence,
  getRsiReviewQueue,
  initializeGenomeBaseline,
  listRsiRuns,
  listRsiStrategies,
  putRsiBindings,
  submitRsiRun,
  type BindingDocument,
  type RsiCandidate,
  type RsiEvidence,
  type RsiReviewItem,
  type RsiRun,
  type RsiStrategy,
} from '@/services/rsi/service';
import { rsiScopeKey, useRsiPanelStore, type RsiScope } from '@/stores/rsiPanelStore';
import { X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

const field = 'w-full rounded-md border border-input bg-background p-2 text-sm';
const pretty = (value: unknown) => JSON.stringify(value, null, 2);

function RsiPanelBody({ scope }: { scope: RsiScope }) {
  const wide = useMediaQuery('(min-width: 1280px)');
  const close = useRsiPanelStore((state) => state.close);
  const [document, setDocument] = useState<BindingDocument | null>(null);
  const [draft, setDraft] = useState('[]');
  const [strategies, setStrategies] = useState<RsiStrategy[]>([]);
  const [bindingId, setBindingId] = useState('');
  const [runs, setRuns] = useState<RsiRun[]>([]);
  const [total, setTotal] = useState(0);
  const [runId, setRunId] = useState('');
  const [evidence, setEvidence] = useState<RsiEvidence | null>(null);
  const [candidates, setCandidates] = useState<RsiCandidate[] | null>(null);
  const [reviewQueue, setReviewQueue] = useState<{ total: number; items: RsiReviewItem[] } | null>(null);
  const [error, setError] = useState('');
  const [baselineNote, setBaselineNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const pending = useRsiPanelStore((state) => state.attempts[rsiScopeKey(scope)]);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const dirty = document !== null && draft !== pretty(document.bindings);

  const loadConfiguration = useCallback(async () => {
    setLoaded(false);
    const [config, catalog] = await Promise.all([getRsiBindings(scope), listRsiStrategies(scope)]);
    if (!alive.current) return;
    setDocument(config);
    setDraft(pretty(config.bindings));
    setStrategies(catalog.strategies);
    setBindingId((id) => (config.bindings.some((item) => item.id === id) ? id : config.bindings[0]?.id ?? ''));
    setLoaded(true);
  }, [scope]);
  useEffect(() => {
    void loadConfiguration().catch((cause: unknown) => {
      if (alive.current) setError(rsiErrorMessage(cause, '配置读取失败，请检查权限和 Evolution 路由。'));
    });
  }, [loadConfiguration]);

  useEffect(() => {
    if (!loaded) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setEvidence(null);
    setCandidates(null);
    const poll = async () => {
      try {
        const list = await listRsiRuns(scope);
        const queue = await getRsiReviewQueue(scope).catch(() => null);
        const detail = runId ? await Promise.all([getRsiEvidence(scope, runId), getRsiCandidates(scope, runId)]) : null;
        if (!active) return;
        setRuns(list.runs);
        setTotal(list.total);
        if (queue) setReviewQueue(queue);
        if (detail) {
          setEvidence(detail[0]);
          setCandidates(detail[1].candidates);
        }
      } catch (cause) {
        if (active) setError(rsiErrorMessage(cause, '运行记录读取失败。界面状态不是服务端状态，请刷新核对。'));
      } finally {
        if (active) timer = setTimeout(() => void poll(), 3000);
      }
    };
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [scope, runId, loaded]);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (cause) {
      if (alive.current)
        setError(
          rsiErrorMessage(
            cause,
            '操作未确认：请检查权限、配置和服务日志。保存冲突需重新读取；提交重试会复用同一幂等键。',
          ),
        );
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  /** 冷启动兜底：缺基线时先走基因组侧的显式建基线（不 apply），再原样重试一次。 */
  async function withBaselineRetry(action: () => Promise<void>) {
    try {
      await action();
    } catch (cause) {
      if (rsiDomainCode(cause) !== 'no_genome_baseline') throw cause;
      setBaselineNote('这个 Bot 还没有基因组基线，正在以当前配置建立初始基线（不改动运行内容）…');
      try {
        await initializeGenomeBaseline(scope);
        await action();
      } catch (retryCause) {
        if (alive.current) setBaselineNote('');
        throw retryCause;
      }
      if (alive.current) setBaselineNote('初始基线已建立（只记录了当前配置，Bot 运行内容未改动）。');
    }
  }
  async function save() {
    if (!document) return;
    const parsed: unknown = JSON.parse(draft);
    if (!Array.isArray(parsed)) throw new Error('bindings must be an array');
    await withBaselineRetry(async () => {
      const saved = await putRsiBindings(scope, { bindings: parsed, etag: document.etag });
      if (!alive.current) return;
      setDocument(saved);
      setDraft(pretty(saved.bindings));
      setBindingId(saved.bindings[0]?.id ?? '');
    });
  }
  async function submit() {
    const attempt = useRsiPanelStore.getState().beginAttempt(scope, bindingId);
    // 幂等键在重试间保持不变：建基线前后是同一次提交。
    await withBaselineRetry(async () => {
      const result = await submitRsiRun(scope, attempt.bindingId, attempt.key);
      useRsiPanelStore.getState().confirmAttempt(scope, attempt.key);
      if (!alive.current) return;
      setRunId(result.run);
    });
  }
  const selected = runs.find((item) => item.id === runId);
  return (
    <aside
      aria-label="RSI任务副屏"
      role="region"
      style={{ width: wide ? 480 : undefined }}
      className="absolute inset-0 z-30 flex min-h-0 min-w-0 flex-col border-l bg-card text-card-foreground xl:static xl:shrink-0"
    >
      <header className="flex items-center justify-between border-b p-4">
        <h2 className="font-semibold">RSI任务</h2>
        <Button variant="ghost" size="icon" aria-label="关闭RSI任务" onClick={close}>
          <X className="size-4" />
        </Button>
      </header>
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <p className="break-all text-xs text-muted-foreground">
          Bot：{scope.botId} / 归属：{scope.ownerId}
          <br />
          Bot 级任务，不按聊天会话隔离。completed 仅表示策略结束，不代表评测通过或发布。
        </p>
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
        {baselineNote ? <p className="text-xs text-muted-foreground">{baselineNote}</p> : null}
        <GenomeBaselinePanel scope={scope} />
        <section className="space-y-2" aria-label="RSI配置">
          <h3 className="font-medium">策略与绑定</h3>
          <p className="text-xs text-muted-foreground">
            已注册：{strategies.map((item) => `${item.id}@${item.version}（${item.conformance}）`).join('、') || '无'}
          </p>
          <label className="block text-sm" htmlFor="rsi-bindings">
            绑定配置（完整 JSON 数组）
          </label>
          <textarea
            id="rsi-bindings"
            className={`${field} min-h-48 font-mono text-xs`}
            value={draft}
            disabled={!loaded || busy || Boolean(pending)}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" disabled={busy || Boolean(pending)} onClick={() => void act(loadConfiguration)}>
              重新读取配置
            </Button>
            <Button disabled={!loaded || busy || !dirty || Boolean(pending)} onClick={() => void act(save)}>
              保存配置
            </Button>
            <Button
              variant="outline"
              disabled={!loaded || busy || Boolean(pending)}
              onClick={() => {
                const strategy = strategies.find((item) => item.enabled);
                if (!strategy || !document) return;
                setDraft(
                  pretty([
                    ...document.bindings,
                    {
                      id: `manual-${Date.now()}`,
                      strategy: `${strategy.id}@${strategy.version}`,
                      trigger: { manual: true },
                      parent: 'active',
                      allowed_genes: ['persona'],
                      verification_profile: 'rules-file@1',
                      budget: { max_usd: 0.1, max_wall_clock_s: 60 },
                      params: {},
                      auto_promote_ceiling: null,
                    },
                  ]),
                );
              }}
            >
              新增手动绑定示例
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            示例默认仅允许 persona，不是本地 Skill 的现成配置。修改 Skill 时须按注册能力配置 allowed_genes / mutation_scope、params 和预算；保存使用 ETag，冲突时不会覆盖别人的修改。
          </p>
          <label className="block text-sm" htmlFor="rsi-binding">
            运行绑定
          </label>
          <select
            id="rsi-binding"
            className={field}
            value={bindingId}
            disabled={busy || Boolean(pending)}
            onChange={(event) => setBindingId(event.target.value)}
          >
            <option value="">请选择已保存的绑定</option>
            {document?.bindings.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id} · {item.strategy}
              </option>
            ))}
          </select>
          <Button disabled={!loaded || busy || (!pending && (!bindingId || dirty))} onClick={() => void act(submit)}>
            {pending ? '重试同一次提交' : '启动RSI任务'}
          </Button>
          {pending ? (
            <p className="break-all text-xs text-muted-foreground">
              提交待确认，幂等键：{pending.key}
              。关闭/刷新面板仍会在当前浏览器标签页保留此键；请重试确认，不要新开标签重复提交。
            </p>
          ) : null}
        </section>
        <RsiReviewQueue queue={reviewQueue} onSelect={setRunId} />
        <section className="space-y-2" aria-label="RSI运行记录">
          <h3 className="font-medium">
            最近运行（{runs.length}/{total}）
          </h3>
          <RsiRunList runs={runs} selectedId={runId} onSelect={setRunId} />
          {selected ? <RsiRunDetails scope={scope} selected={selected} evidence={evidence}
            candidates={candidates} act={act} busy={busy} /> : null}
        </section>
      </div>
    </aside>
  );
}

export function RsiPanel({ scope, enabled }: { scope?: RsiScope; enabled: boolean }) {
  const opened = useRsiPanelStore((state) => state.scope);
  if (!enabled || !scope || !opened || rsiScopeKey(scope) !== rsiScopeKey(opened)) return null;
  // Key prevents late reads and drafts from a different Bot leaking into this one.
  return <RsiPanelBody key={rsiScopeKey(scope)} scope={opened} />;
}
