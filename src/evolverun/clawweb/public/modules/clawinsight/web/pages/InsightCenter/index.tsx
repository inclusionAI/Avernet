import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { insightApi } from "../../api/insight";
import { useClientUser } from "@avernet/clawweb-shared/web/hooks/useClientUser";
import type { InsightScopeParams } from "../../types/insight";
import AdminReviewQueue from "./AdminReviewQueue";
import FailureTasks from "./FailureTasks";
import ImprovementItems from "./ImprovementItems";
import InsightOverview from "./InsightOverview";
import { InsightIcon } from "./InsightUi";

type InsightTab = "todo" | "evidence" | "overview" | "admin";
type BotOption = { botId: string; botName: string; ownerUserId?: string };

type TabConfig = {
  value: InsightTab;
  label: string;
  description: string;
  icon: "clipboard" | "warning" | "chart" | "judge";
  adminOnly?: boolean;
};

const tabs: TabConfig[] = [
  { value: "overview", label: "效果概览", description: "分析完成率与失败趋势", icon: "chart" },
  { value: "evidence", label: "问题证据", description: "查看失败案例与原始时间线", icon: "warning" },
  { value: "todo", label: "我的待办", description: "处理已经确认的修复事项", icon: "clipboard" },
  { value: "admin", label: "管理", description: "审核候选改进项与全局事项", icon: "judge", adminOnly: true },
];

function parseTab(value: string | null, isAdmin: boolean): InsightTab {
  const compatible = value === "improvements" ? "todo" : value === "failures" ? "evidence" : value;
  if (compatible === "admin") return isAdmin ? "admin" : "overview";
  if (compatible === "todo" || compatible === "evidence" || compatible === "overview") return compatible;
  return "overview";
}

function parseImprovementId(value: string | null): number | undefined {
  if (!value || !/^\d+$/.test(value)) return undefined;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : undefined;
}

export default function InsightCenter() {
  const navigate = useNavigate();
  const { user } = useClientUser();
  const isAdmin = user?.isAdmin === true;
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = parseTab(searchParams.get("tab"), isAdmin);
  const ownerUserId = searchParams.get("ownerUserId") || undefined;
  const botId = searchParams.get("botId") || undefined;
  const from = searchParams.get("from") || undefined;
  const to = searchParams.get("to") || undefined;
  const failureClass = searchParams.get("failureClass") || undefined;
  const isCronRaw = searchParams.get("isCron");
  const isCron = isCronRaw === "true" ? true : isCronRaw === "false" ? false : undefined;
  const selectedImprovementId = parseImprovementId(searchParams.get("improvementId"));
  const scope = useMemo<InsightScopeParams>(() => ({ ownerUserId, botId, from, to, isCron }), [ownerUserId, botId, from, to, isCron]);
  const [botOptions, setBotOptions] = useState<BotOption[]>([]);
  const [dataAsOf, setDataAsOf] = useState("");
  const [ownerInput, setOwnerInput] = useState(ownerUserId || "*");

  useEffect(() => {
    let active = true;
    insightApi.overview(ownerUserId ? { ownerUserId } : {}).then((result) => {
      if (!active) return;
      setBotOptions(result.botComparison.map((bot) => ({ botId: bot.botId, botName: bot.botName, ownerUserId: bot.ownerUserId })));
      setDataAsOf(result.dataAsOf);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [ownerUserId]);

  const updateParams = (updates: Record<string, string | number | boolean | undefined>, replace = false) => {
    const next = new URLSearchParams(searchParams);
    Object.entries(updates).forEach(([key, value]) => {
      if (value === undefined || value === "") next.delete(key);
      else next.set(key, String(value));
    });
    setSearchParams(next, { replace });
  };

  const setScope = (patch: InsightScopeParams & { failureClass?: string }) => {
    if (Object.prototype.hasOwnProperty.call(patch, "ownerUserId")) {
      setOwnerInput(patch.ownerUserId || "*");
    }
    updateParams({ ownerUserId: patch.ownerUserId, botId: patch.botId, from: patch.from, to: patch.to, isCron: patch.isCron, failureClass: patch.failureClass }, true);
  };

  const visibleTabs = tabs.filter((tab) => !tab.adminOnly || isAdmin);

  return <div className="mx-auto max-w-screen-2xl px-4 py-7 sm:px-6 lg:px-8">
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div><div className="mb-2 flex items-center gap-2 text-sm font-medium text-blue-600"><InsightIcon name="chart" />ClawWeb Insight Center</div><h1 className="text-2xl font-semibold tracking-tight text-gray-950">Agent 治理与效果中心</h1><p className="mt-1.5 text-sm text-gray-500">先看整体效果，再下钻问题证据，最后处理已经确认的修复事项。</p>{dataAsOf && <p className="mt-1 text-[11px] text-gray-400">数据水位：{dataAsOf}</p>}</div>
      <button onClick={() => navigate("/evolve")} className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3.5 py-2.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50">Claw 进化室<InsightIcon name="external" /></button>
    </div>

    <nav className={`mb-5 grid overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm ${visibleTabs.length === 4 ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}>
      {visibleTabs.map((tab) => {
        const active = activeTab === tab.value;
        return <button key={tab.value} onClick={() => updateParams({ tab: tab.value, improvementId: tab.value === "todo" ? selectedImprovementId : undefined })} className={`relative flex items-center gap-3 border-b border-gray-100 px-5 py-4 text-left transition last:border-b-0 sm:border-b-0 sm:border-r sm:last:border-r-0 ${active ? tab.value === "admin" ? "bg-amber-50/80" : "bg-blue-50/70" : "hover:bg-gray-50"}`}><span className={`flex h-9 w-9 items-center justify-center rounded-xl ${active ? tab.value === "admin" ? "bg-amber-600 text-white" : "bg-blue-600 text-white" : "bg-gray-100 text-gray-500"}`}><InsightIcon name={tab.icon} /></span><div><span className={`text-xs font-semibold ${active ? tab.value === "admin" ? "text-amber-900" : "text-blue-900" : "text-gray-800"}`}>{tab.label}</span><p className="mt-1 text-[11px] text-gray-400">{tab.description}</p></div>{active && <span className={`absolute inset-x-0 bottom-0 h-0.5 ${tab.value === "admin" ? "bg-amber-600" : "bg-blue-600"}`} />}</button>;
      })}
    </nav>

    {isAdmin && activeTab !== "admin" && <section className="mb-5 rounded-2xl border border-amber-200 bg-amber-50/70 p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><div className="flex items-center gap-2 text-sm font-semibold text-amber-950"><InsightIcon name="users" />统一数据视角</div><p className="mt-1 text-xs leading-5 text-amber-800">当前范围会同时作用于效果概览、问题证据和我的待办；切换 Tab 不会丢失。管理员查看他人的待办时仅可读。</p></div>
        <div className="inline-flex rounded-lg border border-amber-200 bg-white p-1 text-xs font-medium">
          <button type="button" onClick={() => { setOwnerInput("*"); updateParams({ ownerUserId: undefined, botId: undefined, improvementId: undefined }, true); }} className={`rounded-md px-3 py-1.5 ${!ownerUserId ? "bg-gray-900 text-white" : "text-gray-600 hover:bg-gray-50"}`}>我的视角</button>
          <button type="button" onClick={() => updateParams({ ownerUserId: ownerInput.trim() || "*", botId: undefined, improvementId: undefined }, true)} className={`rounded-md px-3 py-1.5 ${ownerUserId ? "bg-amber-600 text-white" : "text-gray-600 hover:bg-gray-50"}`}>管理视角</button>
        </div>
      </div>
      {ownerUserId && <div className="mt-4 grid gap-3 md:grid-cols-[minmax(260px,1fr)_auto]">
        <label><span className="mb-1.5 block text-xs font-medium text-amber-900">查看范围 user_id</span><input value={ownerInput} onChange={(event) => setOwnerInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && ownerInput.trim()) updateParams({ ownerUserId: ownerInput.trim(), botId: undefined, improvementId: undefined }, true); }} placeholder="输入用户工号/账号，* 表示全部用户" className="w-full rounded-lg border border-amber-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-amber-500" /></label>
        <button disabled={!ownerInput.trim() || ownerInput.trim() === ownerUserId} onClick={() => updateParams({ ownerUserId: ownerInput.trim(), botId: undefined, improvementId: undefined }, true)} className="self-end rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40">应用用户范围</button>
        <p className="text-[11px] text-amber-800 md:col-span-2">当前查看：<span className="font-mono font-semibold">{ownerUserId === "*" ? "全部用户" : ownerUserId}</span></p>
      </div>}
    </section>}

    {activeTab === "todo" && <ImprovementItems ownerUserId={ownerUserId} readOnly={Boolean(ownerUserId)} botId={botId} selectedImprovementId={selectedImprovementId} botOptions={botOptions} onBotChange={(value) => updateParams({ botId: value, improvementId: undefined }, true)} onSelectImprovement={(value) => updateParams({ improvementId: value }, true)} onGoFailures={() => updateParams({ tab: "evidence", improvementId: undefined })} />}
    {activeTab === "evidence" && <FailureTasks externalAudienceScope scope={scope} failureClass={failureClass} botOptions={botOptions} onScopeChange={(patch) => setScope({ ...scope, ...patch })} onImprovementCreated={(improvementId) => updateParams({ tab: "todo", improvementId })} />}
    {activeTab === "overview" && <InsightOverview isAdmin={isAdmin} scope={scope} botOptions={botOptions} onScopeChange={(patch) => setScope({ ...scope, ...patch, failureClass })} onFailureDrilldown={(value) => updateParams({ tab: "evidence", failureClass: value })} />}
    {activeTab === "admin" && isAdmin && <AdminReviewQueue
      botOptions={botOptions}
      selectedImprovementId={selectedImprovementId}
      onSelectImprovement={(value) => updateParams({ improvementId: value }, true)}
    />}
  </div>;
}
