import { useEffect, useMemo, useState } from "react";
import { insightApi } from "../../api/insight";
import type { FailureTaskIndex, ImprovementDetail, ImprovementView } from "../../types/insight";
import FailureTaskDrawer from "./FailureTaskDrawer";
import { EmptyPanel, ErrorPanel, InsightIcon, LoadingPanel } from "./InsightUi";
import { createRequestId, failureClassText, formatDateTime } from "./utils";

type BotOption = { botId: string; botName: string };
type ReviewFilter = "PENDING" | "APPROVED" | "REJECTED" | "ALL" | "ALL_ITEMS";
type ReviewDecision = "APPROVE" | "REJECT";

type AdminReviewQueueProps = {
  botOptions: BotOption[];
  selectedImprovementId?: number;
  onSelectImprovement: (improvementId: number | undefined) => void;
};

const reviewTabs: Array<{ value: ReviewFilter; label: string }> = [
  { value: "PENDING", label: "待审核" },
  { value: "APPROVED", label: "已批准" },
  { value: "REJECTED", label: "已驳回" },
  { value: "ALL", label: "全部" },
  { value: "ALL_ITEMS", label: "全部改进项" },
];

export default function AdminReviewQueue({
  botOptions,
  selectedImprovementId,
  onSelectImprovement,
}: AdminReviewQueueProps) {
  const [filter, setFilter] = useState<ReviewFilter>("PENDING");
  const [ownerUserId, setOwnerUserId] = useState("");
  const [botId, setBotId] = useState("");
  const [items, setItems] = useState<ImprovementView[]>([]);
  const [counts, setCounts] = useState({ pending: 0, approved: 0, rejected: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [detail, setDetail] = useState<ImprovementDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [evidenceTask, setEvidenceTask] = useState<FailureTaskIndex | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(() => new Set());
  const [reviewing, setReviewing] = useState<{
    items: ImprovementView[];
    decision: ReviewDecision;
  } | null>(null);
  const [executing, setExecuting] = useState<ImprovementDetail | null>(null);
  const [executeMessage, setExecuteMessage] = useState("");
  const [consentLink, setConsentLink] = useState<{ url: string; expiresAt: string } | null>(null);
  const [consentLinkLoading, setConsentLinkLoading] = useState(false);
  const botNames = useMemo(
    () => new Map(botOptions.map((bot) => [bot.botId, bot.botName])),
    [botOptions],
  );

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setLoading(true);
      setError("");
      insightApi.adminImprovements({
        ownerUserId: ownerUserId.trim() || undefined,
        botId: botId || undefined,
        adminReviewStatus: ["ALL", "ALL_ITEMS"].includes(filter) ? undefined : filter,
        includeAll: filter === "ALL_ITEMS",
        pageSize: 50,
      }).then((result) => {
        if (!active) return;
        setItems(result.items);
        setCounts({
          pending: result.reviewCounts.pending,
          approved: result.reviewCounts.approved,
          rejected: result.reviewCounts.rejected,
        });
        const visiblePendingIds = new Set(
          result.items
            .filter((item) => item.adminReviewStatus === "PENDING")
            .map((item) => item.improvementId),
        );
        setSelectedIds((current) => new Set([...current].filter((id) => visiblePendingIds.has(id))));
      }).catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Admin 队列加载失败");
      }).finally(() => {
        if (active) setLoading(false);
      });
    });
    return () => { active = false; };
  }, [filter, ownerUserId, botId, reloadKey]);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      if (!selectedImprovementId) {
        setDetail(null);
        return;
      }
      setDetailLoading(true);
      insightApi.adminImprovement(selectedImprovementId)
        .then((value) => { if (active) setDetail(value); })
        .catch(() => { if (active) setDetail(null); })
        .finally(() => { if (active) setDetailLoading(false); });
    });
    return () => { active = false; };
  }, [selectedImprovementId, reloadKey]);

  const pendingItems = items.filter((item) => item.adminReviewStatus === "PENDING");
  const selectedItems = pendingItems.filter((item) => selectedIds.has(item.improvementId));
  const allPendingSelected = pendingItems.length > 0
    && pendingItems.every((item) => selectedIds.has(item.improvementId));

  const countFor = (value: ReviewFilter) => {
    if (value === "PENDING") return counts.pending;
    if (value === "REJECTED") return counts.rejected;
    if (value === "APPROVED") return counts.approved;
    if (value === "ALL_ITEMS") return items.length;
    return counts.pending + counts.approved + counts.rejected;
  };

  const toggleSelection = (improvementId: number) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(improvementId)) next.delete(improvementId);
      else next.add(improvementId);
      return next;
    });
  };

  const toggleAllPending = () => {
    setSelectedIds(allPendingSelected
      ? new Set()
      : new Set(pendingItems.map((item) => item.improvementId)));
  };

  const reviewDone = () => {
    setReviewing(null);
    setSelectedIds(new Set());
    onSelectImprovement(undefined);
    setReloadKey((value) => value + 1);
  };

  const executeDone = (taskId: string) => {
    setExecuting(null);
    setExecuteMessage(`已创建一次性管理员代处理任务 ${taskId}，不会创建用户持续授权。`);
    setReloadKey((value) => value + 1);
  };

  const openEvidence = (improvement: ImprovementDetail, evidence: ImprovementDetail["evidence"][number]) => {
    const sourceDt = improvement.dataAsOf.slice(0, 10).replaceAll("-", "");
    setEvidenceTask({
      sourceDt,
      ownerUserId: improvement.botOwnerUserId,
      botId: improvement.botId,
      botName: botNames.get(improvement.botId) || improvement.botId,
      sessionId: evidence.sessionId,
      taskIndex: evidence.taskIndex,
      taskDescription: evidence.taskDescription,
      isComplete: 0,
      failureClass: evidence.failureClass,
      judgeReasonSummary: evidence.reasoningSummary,
      sessionStartTime: improvement.dataStartTime,
      sessionEndTime: improvement.dataEndTime,
      sessionDurationSeconds: null,
      isCron: false,
      dataAsOf: improvement.dataAsOf,
    });
  };

  return <div className="space-y-5">
    <section className="overflow-hidden rounded-2xl border border-amber-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-amber-100 bg-amber-50/60 px-5 py-4">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-amber-900">
            <InsightIcon name="judge" />改进项审核
          </div>
          <p className="mt-1 text-xs text-amber-700">Governance Agent 创建的候选改进项。批准前仅管理员可见，不会派发给用户，也不会进入进化室。</p>
        </div>
        <button onClick={() => setReloadKey((value) => value + 1)} className="inline-flex items-center gap-2 rounded-lg border border-amber-200 bg-white px-3 py-2 text-xs font-medium text-amber-800 hover:bg-amber-50">
          <InsightIcon name="refresh" />刷新
        </button>
      </div>

      <div className="grid gap-3 border-b border-gray-100 px-5 py-4 md:grid-cols-[1fr_1fr_auto]">
        <input value={ownerUserId} onChange={(event) => setOwnerUserId(event.target.value)} placeholder="按拟派发用户筛选" className="rounded-lg border border-gray-200 px-3 py-2.5 text-xs outline-none focus:border-amber-500" />
        <select value={botId} onChange={(event) => setBotId(event.target.value)} className="rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-xs outline-none focus:border-amber-500">
          <option value="">全部 Bot</option>
          {botOptions.map((bot) => <option key={bot.botId} value={bot.botId}>{bot.botName}</option>)}
        </select>
        <div className="text-right text-xs text-gray-400">当前 {items.length} 条</div>
      </div>

      <div className="flex gap-6 overflow-x-auto px-5">
        {reviewTabs.map((tab) => <button key={tab.value} onClick={() => setFilter(tab.value)} className={`border-b-2 py-3 text-xs font-medium ${filter === tab.value ? "border-amber-600 text-amber-800" : "border-transparent text-gray-500 hover:text-gray-800"}`}>
          {tab.label}<span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-600">{countFor(tab.value)}</span>
        </button>)}
      </div>

      {executeMessage && <div className="border-b border-emerald-100 bg-emerald-50 px-5 py-3 text-xs leading-5 text-emerald-800">{executeMessage}</div>}

      {filter === "PENDING" && pendingItems.length > 0 && <div className="flex flex-wrap items-center gap-3 border-y border-amber-100 bg-amber-50/40 px-5 py-3">
        <label className="inline-flex cursor-pointer items-center gap-2 text-xs font-medium text-gray-700">
          <input type="checkbox" checked={allPendingSelected} onChange={toggleAllPending} className="h-4 w-4 rounded border-gray-300" />
          全选当前页
        </label>
        <span className="text-xs text-gray-500">已选择 {selectedItems.length} 项</span>
        <div className="ml-auto flex flex-wrap gap-2">
          <button disabled={selectedItems.length === 0} onClick={() => setReviewing({ items: selectedItems, decision: "APPROVE" })} className="rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-semibold text-amber-800 hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-40">批量批准</button>
          <button disabled={selectedItems.length === 0} onClick={() => setReviewing({ items: selectedItems, decision: "REJECT" })} className="rounded-lg border border-red-200 bg-white px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40">批量驳回</button>
          <button onClick={() => setReviewing({ items: pendingItems, decision: "APPROVE" })} className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-700">全部批准</button>
        </div>
      </div>}

      {loading
        ? <LoadingPanel text="正在读取候选改进项…" />
        : error
          ? <ErrorPanel message={error} onRetry={() => setReloadKey((value) => value + 1)} />
          : items.length === 0
            ? <EmptyPanel title="当前没有需要审核的改进项" description="Governance Agent 的无效 Case 会在内部结束，不会进入这里。" />
            : <div className="divide-y divide-gray-100">
              {items.map((item) => <article key={item.improvementId} className="grid gap-4 px-5 py-4 md:grid-cols-[auto_minmax(0,2fr)_180px_160px_auto]">
                <div className="flex items-start pt-1">
                  {item.adminReviewStatus === "PENDING" && <input aria-label={`选择 ${item.title}`} type="checkbox" checked={selectedIds.has(item.improvementId)} onChange={() => toggleSelection(item.improvementId)} className="h-4 w-4 rounded border-gray-300" />}
                </div>
                <button onClick={() => onSelectImprovement(item.improvementId)} className="min-w-0 text-left">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-semibold text-gray-900">{item.title}</p>
                    <span className={`rounded-full px-2.5 py-1 text-[10px] font-medium ${item.actionType === "DIRECT_EVOLUTION" ? "bg-emerald-50 text-emerald-700" : "bg-orange-50 text-orange-700"}`}>{item.actionType === "DIRECT_EVOLUTION" ? "自动优化" : "手动优化"}</span>
                  </div>
                  <p className="mt-1 text-xs text-gray-500">{botNames.get(item.botId) || item.botId} · {item.sessionCount} 个 Session · 规则 {item.sourceRuleId || "—"}</p>
                  <p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-600">{item.rootCauseSummary || item.assignmentReason || item.userGuidance || "暂无补充说明"}</p>
                  <span className="mt-2 inline-block text-xs font-medium text-blue-600">查看改进项详情</span>
                </button>
                <div>
                  <p className="text-[10px] text-gray-400">拟派发用户</p>
                  <p className="mt-1 font-mono text-xs font-medium text-gray-700">{item.ownerUserId}</p>
                  <p className="mt-2 text-[10px] text-gray-400">批准前用户不可见</p>
                </div>
                <div>
                  <p className="text-[10px] text-gray-400">审核状态</p>
                  <p className="mt-1 text-xs font-medium text-gray-700">{item.adminReviewStatus === "PENDING" ? "待审核" : item.adminReviewStatus === "REJECTED" ? "已驳回" : "已批准"}</p>
                  <p className="mt-2 text-[10px] text-gray-400">创建：{formatDateTime(item.gmtCreate)}</p>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  {item.adminReviewStatus === "PENDING" ? <>
                    <button onClick={() => setReviewing({ items: [item], decision: "APPROVE" })} className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-semibold text-white hover:bg-amber-700">批准</button>
                    <button onClick={() => setReviewing({ items: [item], decision: "REJECT" })} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50">驳回</button>
                  </> : <button onClick={() => onSelectImprovement(item.improvementId)} className="text-xs font-medium text-blue-600">查看详情</button>}
                </div>
              </article>)}
            </div>}
    </section>

    {selectedImprovementId && <div className="fixed inset-0 z-[65] bg-gray-950/25" onMouseDown={(event) => { if (event.target === event.currentTarget) onSelectImprovement(undefined); }}>
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-3xl flex-col bg-gray-50 shadow-2xl">
        <header className="flex items-center justify-between border-b border-gray-200 bg-white px-5 py-4">
          <div>
            <p className="text-xs font-medium text-amber-700">候选改进项详情</p>
            <p className="mt-1 font-mono text-[10px] text-gray-400">ITEM-{selectedImprovementId}</p>
          </div>
          <button onClick={() => onSelectImprovement(undefined)} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><InsightIcon name="close" /></button>
        </header>
        <div className="flex-1 overflow-y-auto p-5">
          {detailLoading
            ? <LoadingPanel />
            : detail
              ? <div className="space-y-5">
                <section className="rounded-2xl border border-amber-200 bg-amber-50/60 p-5">
                  <p className="text-xs font-semibold text-amber-900">批准前仅管理员可见</p>
                  <p className="mt-1 text-xs leading-5 text-amber-800">这是 Governance Agent 创建的候选改进项。批准后才会正式派发给用户；驳回后不会进入用户待办。</p>
                </section>
                <section className="rounded-2xl border border-gray-200 bg-white p-5">
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[10px] font-medium text-amber-700">{detail.adminReviewStatus === "PENDING" ? "待审核" : detail.adminReviewStatus === "REJECTED" ? "已驳回" : "已批准"}</span>
                    <span className="text-[11px] text-gray-400">{detail.actionType === "DIRECT_EVOLUTION" ? "自动优化" : "手动优化"}</span>
                  </div>
                  <h2 className="mt-3 text-xl font-semibold text-gray-950">{detail.title}</h2>
                  <p className="mt-3 text-xs text-gray-500">拟派发给 <span className="font-mono font-semibold text-gray-700">{detail.ownerUserId}</span> · Bot <span className="font-mono font-semibold text-gray-700">{detail.botId}</span></p>
                </section>
                <section className="rounded-2xl border border-gray-200 bg-white p-5">
                  <h3 className="text-sm font-semibold text-gray-900">Governance Agent 判断</h3>
                  <dl className="mt-3 space-y-3 text-sm leading-6">
                    <div><dt className="text-xs font-medium text-gray-400">根因</dt><dd className="mt-1 text-gray-700">{detail.rootCauseSummary || "—"}</dd></div>
                    <div><dt className="text-xs font-medium text-gray-400">为什么要创建</dt><dd className="mt-1 text-gray-700">{detail.assignmentReason || "—"}</dd></div>
                    <div><dt className="text-xs font-medium text-gray-400">建议动作</dt><dd className="mt-1 whitespace-pre-wrap text-gray-700">{detail.suggestedAction || detail.userGuidance || "—"}</dd></div>
                  </dl>
                </section>
                <section className="rounded-2xl border border-gray-200 bg-white p-5">
                  <div className="flex items-start justify-between gap-4"><div><h3 className="text-sm font-semibold text-gray-900">代表证据</h3><p className="mt-1 text-xs text-gray-400">点击证据可查看原始请求、Agent 回复、工具调用和完整行为轨迹。</p></div><span className="text-xs text-gray-400">共 {detail.evidence.length} 条</span></div>
                  <div className="mt-3 space-y-3">
                    {detail.evidence.map((evidence) => <div key={`${evidence.sessionId}:${evidence.taskIndex}`} className="rounded-xl border border-gray-100 bg-gray-50 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-xs font-medium text-gray-800">{evidence.taskDescription}</p>
                        <span className="shrink-0 text-[10px] text-orange-700">{failureClassText[evidence.failureClass] || evidence.failureClass}</span>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-gray-600">{evidence.reasoningSummary || "暂无摘要"}</p>
                      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 pt-3"><span className="font-mono text-[10px] text-gray-400">{evidence.sessionId} · Task {evidence.taskIndex}</span><button onClick={() => openEvidence(detail, evidence)} className="inline-flex items-center gap-1.5 rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs font-semibold text-blue-700 hover:bg-blue-50"><InsightIcon name="external" />查看完整 Bad Case</button></div>
                    </div>)}
                  </div>
                </section>
                {detail.adminReviewStatus === "REJECTED" && detail.rejectComment && <section className="rounded-2xl border border-red-200 bg-red-50 p-5">
                  <h3 className="text-sm font-semibold text-red-900">Admin 驳回理由</h3>
                  <p className="mt-2 text-sm leading-6 text-red-800">{detail.rejectComment}</p>
                </section>}
                {detail.adminReviewStatus !== "PENDING" && detail.status.toUpperCase() === "ACTIVE" && <section className="rounded-2xl border border-blue-200 bg-blue-50/70 p-5">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <h3 className="text-sm font-semibold text-blue-950">管理员代处理</h3>
                      <p className="mt-1 text-xs leading-5 text-blue-800">可以代用户发起一次修复。此次操作不会创建用户持续授权，后续同类问题仍需用户授权或再次由管理员确认。</p>
                    </div>
                    <span className="shrink-0 rounded-full bg-white px-2.5 py-1 text-[10px] font-semibold text-blue-700">一次性</span>
                  </div>
                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-blue-100 bg-white px-4 py-3">
                    <div className="text-xs text-gray-600">目标用户 <span className="font-mono font-semibold text-gray-800">{detail.ownerUserId}</span> · Bot <span className="font-mono font-semibold text-gray-800">{detail.botId}</span></div>
                    <div className="flex flex-wrap justify-end gap-2">
                      {detail.actionType === "DIRECT_EVOLUTION" && <button disabled={consentLinkLoading} onClick={async () => {
                        setConsentLinkLoading(true); setExecuteMessage("");
                        try {
                          const result = await insightApi.adminConsentLink(detail.improvementId);
                          setConsentLink({ url: result.url, expiresAt: result.expiresAt });
                        } catch (cause) {
                          setExecuteMessage(cause instanceof Error ? cause.message : "授权链接生成失败");
                        } finally { setConsentLinkLoading(false); }
                      }} className="rounded-lg border border-emerald-200 bg-white px-3 py-2.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-50">{consentLinkLoading ? "生成中…" : "生成 Owner 持续授权链接"}</button>}
                      <button onClick={() => { setExecuteMessage(""); setExecuting(detail); }} className="rounded-lg bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white hover:bg-blue-700">
                        {detail.actionType === "ASSIGN_OWNER" ? "指定方向并进入进化室" : "代用户发起一次自动修复"}
                      </button>
                    </div>
                  </div>
                  {consentLink && detail.actionType === "DIRECT_EVOLUTION" && <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3"><p className="text-xs font-semibold text-emerald-900">把这个链接发给 Owner，由 Owner 登录后确认持续授权</p><div className="mt-2 flex gap-2"><input readOnly value={consentLink.url} className="min-w-0 flex-1 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-[11px] text-gray-700" /><button onClick={() => void navigator.clipboard?.writeText(consentLink.url)} className="rounded-lg bg-emerald-600 px-3 py-2 text-xs font-semibold text-white hover:bg-emerald-700">复制</button></div><p className="mt-2 text-[10px] text-emerald-700">有效期至 {formatDateTime(consentLink.expiresAt)}。Owner 确认后，后续命中同一规则范围的治理项将自动进入进化室。</p></div>}
                </section>}
                {detail.adminReviewStatus === "PENDING" && <div className="flex flex-wrap justify-end gap-2">
                  <button onClick={() => setReviewing({ items: [detail], decision: "REJECT" })} className="rounded-lg border border-red-200 px-4 py-2.5 text-xs font-semibold text-red-700">驳回</button>
                  <button onClick={() => setReviewing({ items: [detail], decision: "APPROVE" })} className="rounded-lg bg-amber-600 px-4 py-2.5 text-xs font-semibold text-white">批准</button>
                </div>}
              </div>
              : <ErrorPanel message="改进项不存在" />}
        </div>
      </aside>
    </div>}

    {reviewing && <ReviewDialog
      items={reviewing.items}
      decision={reviewing.decision}
      onClose={() => setReviewing(null)}
      onDone={reviewDone}
    />}
    {executing && <AdminExecuteDialog
      item={executing}
      onClose={() => setExecuting(null)}
      onDone={executeDone}
    />}
    {evidenceTask && <FailureTaskDrawer task={evidenceTask} layer="admin" onClose={() => setEvidenceTask(null)} />}
  </div>;
}

export function AdminExecuteDialog({
  item,
  onClose,
  onDone,
}: {
  item: ImprovementDetail;
  onClose: () => void;
  onDone: (taskId: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [repairDirection, setRepairDirection] = useState(item.suggestedAction || item.userGuidance || "");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const isManual = item.actionType === "ASSIGN_OWNER";
  const title = isManual ? "指定方向并进入进化室" : "代用户发起一次自动修复";

  return <div role="dialog" aria-modal="true" aria-label={title} className="fixed inset-0 z-[95] flex items-center justify-center bg-gray-950/40 p-4">
    <div className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-2xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold text-gray-950">{title}</h3>
          <p className="mt-1 text-xs leading-5 text-gray-500">这次操作以管理员身份发起，目标用户为 <span className="font-mono">{item.ownerUserId}</span>，只执行当前改进项一次。</p>
        </div>
        <button onClick={onClose} disabled={submitting} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><InsightIcon name="close" /></button>
      </div>
      <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-900">
        不会创建或复用用户的持续授权；不会因此自动处理后续同类问题；仅允许在当前测试 Bot 上执行，完成后自动进入结果验证。
      </div>
      <div className="mt-5 space-y-4">
        <div className="grid gap-3 rounded-xl border border-gray-100 bg-gray-50 p-4 sm:grid-cols-2">
          <div><p className="text-[10px] text-gray-400">目标用户</p><p className="mt-1 font-mono text-xs font-semibold text-gray-800">{item.ownerUserId}</p></div>
          <div><p className="text-[10px] text-gray-400">执行 Bot</p><p className="mt-1 font-mono text-xs font-semibold text-gray-800">{item.botId}</p></div>
        </div>
        <label className="block"><span className="mb-1.5 block text-xs font-semibold text-gray-700">管理员代处理原因 <span className="text-red-500">*</span></span><textarea value={reason} maxLength={1000} onChange={(event) => setReason(event.target.value)} placeholder="例如：用户长期未处理，该问题已确认且持续影响任务完成率。" className="min-h-24 w-full resize-y rounded-lg border border-gray-200 px-3 py-2.5 text-sm leading-6 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10" /></label>
        <label className="block"><span className="mb-1.5 block text-xs font-semibold text-gray-700">{isManual ? "管理员指定的修复方向" : "本次修复方向"} {isManual && <span className="text-red-500">*</span>}</span><textarea value={repairDirection} maxLength={5000} onChange={(event) => setRepairDirection(event.target.value)} placeholder="说明希望检查或修改的配置、Skill、权限或运行环境，以及不能修改的范围。" className="min-h-28 w-full resize-y rounded-lg border border-gray-200 px-3 py-2.5 text-sm leading-6 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10" /><p className="mt-1 text-[11px] leading-5 text-gray-400">这段内容会写入本次进化任务的修复 Spec，供 Agent 执行。</p></label>
      </div>
      {error && <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} disabled={submitting} className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">取消</button>
        <button disabled={submitting || !reason.trim() || (isManual && !repairDirection.trim())} onClick={async () => {
          setSubmitting(true); setError("");
          try {
            const result = await insightApi.adminExecuteOnce(item.improvementId, {
              reason: reason.trim(),
              repairDirection: repairDirection.trim() || undefined,
            }, createRequestId(`insight-admin-once-${item.improvementId}`));
            onDone(result.taskId);
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : "管理员代处理任务创建失败");
          } finally {
            setSubmitting(false);
          }
        }} className="rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{submitting ? "正在创建…" : "确认并进入进化室"}</button>
      </div>
    </div>
  </div>;
}

function ReviewDialog({
  items,
  decision,
  onClose,
  onDone,
}: {
  items: ImprovementView[];
  decision: ReviewDecision;
  onClose: () => void;
  onDone: () => void;
}) {
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const isBatch = items.length > 1;
  const isReject = decision === "REJECT";
  const title = isReject
    ? (isBatch ? `批量驳回 ${items.length} 个改进项` : "驳回改进项")
    : (isBatch ? `批量批准 ${items.length} 个改进项` : "批准改进项");

  return <div role="dialog" aria-modal="true" aria-label={title} className="fixed inset-0 z-[90] flex items-center justify-center bg-gray-950/40 p-4">
    <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-base font-semibold text-gray-950">{title}</h3>
          <p className="mt-1 text-xs leading-5 text-gray-500">{isBatch ? `本次操作将应用到选中的 ${items.length} 个候选改进项。` : items[0]?.title}</p>
        </div>
        <button onClick={onClose} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><InsightIcon name="close" /></button>
      </div>
      <p className={`mt-4 rounded-xl border px-4 py-3 text-xs leading-5 ${isReject ? "border-red-200 bg-red-50 text-red-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
        {isReject
          ? "驳回后不会派发给用户。驳回理由会作为 Admin 标签保留，供 Governance Agent 后续调整规则。"
          : "批准后改进项才会正式生效。自动优化项命中用户的持续授权时会直接进入治理优化，否则再由用户确认授权。"}
      </p>
      <label className="mt-4 block">
        <span className="mb-1.5 block text-xs font-medium text-gray-600">{isReject ? "驳回理由（必填）" : "审核说明（可选）"}</span>
        <textarea value={comment} onChange={(event) => setComment(event.target.value)} rows={4} maxLength={1000} placeholder={isReject ? "请说明为什么不应派发，以及 Governance Agent 后续应如何调整判断。" : "可补充批准依据。"} className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-amber-500" />
      </label>
      {error && <p className="mt-3 text-xs text-red-600">{error}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2.5 text-xs font-medium text-gray-600">取消</button>
        <button disabled={submitting || (isReject && !comment.trim())} onClick={async () => {
          setSubmitting(true);
          setError("");
          try {
            await Promise.all(items.map((item) => insightApi.reviewAdminImprovement(item.improvementId, {
              decision,
              comment: comment.trim() || undefined,
              version: item.version,
            })));
            onDone();
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "审核失败，请刷新后重试");
          } finally {
            setSubmitting(false);
          }
        }} className={`rounded-lg px-4 py-2.5 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50 ${isReject ? "bg-red-600" : "bg-amber-600"}`}>
          {submitting ? "正在提交…" : "确认"}
        </button>
      </div>
    </div>
  </div>;
}
