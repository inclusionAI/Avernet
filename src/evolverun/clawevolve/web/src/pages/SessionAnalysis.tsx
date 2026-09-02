import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type SessionAnalysisTask } from "../api/client";
import {
  eventContentBlocks,
  eventLabel,
  eventPreview,
  eventRole,
  eventUsageText,
  type BenchSessionEvent,
} from "../bench/session";
import { useClientUser } from "../hooks/useClientUser";
import EvolveBotPicker from "../components/EvolveBotPicker";
import EvolveModelFields, {
  DEFAULT_EVOLVE_MODEL,
  EVOLVE_CUSTOM_MODEL,
} from "../components/EvolveModelFields";
import EvolveTaskOverview from "../components/EvolveTaskOverview";

type SessionBotOption = {
  botId: string;
  botName: string | null;
  env: string | null;
  activeEngine: string | null;
  botType: string | null;
  ownerId?: string | null;
  accessType?: "owner" | "collaborator";
};
const inputClass =
  "w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-900 outline-none transition focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10";
const primaryButton =
  "inline-flex items-center justify-center rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "inline-flex items-center justify-center rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50";

/* react-markdown injects `node`; it must be removed before props reach DOM elements. */
/* eslint-disable @typescript-eslint/no-unused-vars */
const reportMarkdownComponents: Components = {
  h1: ({ node: _node, ...props }) => <h1 className="mb-7 border-b border-gray-200 pb-4 text-3xl font-bold tracking-tight text-gray-950" {...props} />,
  h2: ({ node: _node, ...props }) => <h2 className="mb-4 mt-10 border-b border-gray-100 pb-2 text-xl font-semibold text-gray-950 first:mt-0" {...props} />,
  h3: ({ node: _node, ...props }) => <h3 className="mb-3 mt-7 text-base font-semibold text-gray-900" {...props} />,
  h4: ({ node: _node, ...props }) => <h4 className="mb-2 mt-5 text-sm font-semibold text-gray-900" {...props} />,
  p: ({ node: _node, ...props }) => <p className="my-3 whitespace-pre-wrap text-[15px] leading-7 text-gray-700" {...props} />,
  ul: ({ node: _node, ...props }) => <ul className="my-4 list-disc space-y-2 pl-6 text-[15px] leading-7 text-gray-700 marker:text-blue-500" {...props} />,
  ol: ({ node: _node, ...props }) => <ol className="my-4 list-decimal space-y-2 pl-6 text-[15px] leading-7 text-gray-700 marker:font-semibold marker:text-blue-600" {...props} />,
  li: ({ node: _node, ...props }) => <li className="pl-1" {...props} />,
  strong: ({ node: _node, ...props }) => <strong className="font-semibold text-gray-950" {...props} />,
  blockquote: ({ node: _node, ...props }) => <blockquote className="my-5 rounded-r-xl border-l-4 border-amber-400 bg-amber-50 px-4 py-2 text-amber-950" {...props} />,
  a: ({ node: _node, ...props }) => <a className="font-medium text-blue-600 underline decoration-blue-200 underline-offset-2 hover:text-blue-700" target="_blank" rel="noopener noreferrer" {...props} />,
  hr: ({ node: _node, ...props }) => <hr className="my-8 border-gray-200" {...props} />,
  pre: ({ node: _node, ...props }) => <pre className="my-5 max-h-[520px] overflow-auto rounded-xl border border-gray-800 bg-gray-950 p-4 text-xs leading-6 text-gray-100 shadow-inner [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-inherit" {...props} />,
  code: ({ node: _node, className, ...props }) => <code className={`${className ?? ""} rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[0.9em] text-rose-700`} {...props} />,
  table: ({ node: _node, ...props }) => <div className="my-6 overflow-x-auto rounded-xl border border-gray-200"><table className="w-full border-collapse text-left text-sm" {...props} /></div>,
  thead: ({ node: _node, ...props }) => <thead className="bg-gray-50 text-gray-700" {...props} />,
  tbody: ({ node: _node, ...props }) => <tbody className="divide-y divide-gray-100 bg-white" {...props} />,
  tr: ({ node: _node, ...props }) => <tr className="divide-x divide-gray-100" {...props} />,
  th: ({ node: _node, ...props }) => <th className="whitespace-nowrap px-4 py-3 font-semibold text-gray-900" {...props} />,
  td: ({ node: _node, ...props }) => <td className="min-w-32 px-4 py-3 align-top leading-6 text-gray-700" {...props} />,
};
/* eslint-enable @typescript-eslint/no-unused-vars */

function normalizeDiagnosisMarkdown(markdown: string): string {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const output: string[] = [];
  let inFence = false;
  let hasTitle = false;
  const summaryField = /^(判断|主分类|一句话根因|影响|输入|实际 Session ID|时间范围|事件总数|证据事件|用户目标|预期完成条件|实际结果)[：:]\s*(.*)$/;
  const appendSeparated = (line: string) => {
    if (output.length && output.at(-1)?.trim()) output.push("");
    output.push(line, "");
  };
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      inFence = !inFence;
      output.push(line);
      continue;
    }
    if (inFence || !trimmed) {
      output.push(line);
      continue;
    }
    if (!hasTitle) {
      hasTitle = true;
      if (!trimmed.startsWith("#")) {
        appendSeparated(`# ${trimmed}`);
        continue;
      }
    }
    const section = trimmed.match(/^(\d+)[.、]\s*(.+)$/);
    if (section) {
      appendSeparated(`## ${section[1]}. ${section[2]}`);
      continue;
    }
    if (trimmed.startsWith("⚠")) {
      appendSeparated(`> **证据完整性提醒**  ${trimmed.replace(/^⚠\s*(截断标记[：:]?)?\s*/, "")}`);
      continue;
    }
    const fieldMatch = trimmed.match(summaryField);
    if (fieldMatch) {
      output.push(`- **${fieldMatch[1]}：** ${fieldMatch[2]}`);
      continue;
    }
    if (trimmed === "无截断") {
      output.push("- **证据完整性：** 无截断");
      continue;
    }
    output.push(line);
  }
  return output.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function diagnosis(task: SessionAnalysisTask): Record<string, unknown> {
  const analysis = task.result?.analysis as Record<string, unknown> | undefined;
  return (analysis?.diagnosis as Record<string, unknown> | undefined) ?? {};
}

const field = (value: unknown, fallback = "—") =>
  typeof value === "string" && value ? value : fallback;

function artifactLabel(name: string, mode: SessionAnalysisTask["mode"]) {
  if (name === "raw")
    return mode === "EXPORT_ALL" ? "下载 Session 压缩包" : "下载 Session 文件";
  if (name === "report") return "下载诊断报告";
  if (name === "analysis") return "下载分析 JSON";
  return `下载 ${name}`;
}

function taskStatus(status: string) {
  if (status === "completed" || status === "succeeded")
    return {
      label: "已完成",
      className: "bg-emerald-50 text-emerald-700",
      node: "border-emerald-200 bg-emerald-50/60",
    };
  if (status === "failed")
    return {
      label: "失败",
      className: "bg-violet-50 text-violet-700",
      node: "border-red-200 bg-red-50/50",
    };
  if (status === "canceled")
    return {
      label: "已取消",
      className: "bg-gray-100 text-gray-600",
      node: "border-gray-200 bg-gray-50",
    };
  return {
    label: "运行中",
    className: "bg-blue-50 text-blue-700",
    node: "border-blue-200 bg-blue-50/50",
  };
}

function DetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-4 py-1.5 text-xs">
      <dt className="text-gray-500">{label}</dt>
      <dd
        className={`min-w-0 break-all text-right font-medium text-gray-900 ${mono ? "font-mono" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}

function formatTime(value: number | string | null | undefined): string {
  if (value == null || value === "") return "—";
  const date = new Date(
    typeof value === "number" && value < 10_000_000_000 ? value * 1000 : value,
  );
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString("zh-CN", { hour12: false });
}

function CreateSessionAnalysis() {
  const navigate = useNavigate();
  const { user } = useClientUser();
  const [bots, setBots] = useState<SessionBotOption[]>([]);
  const [adminMode, setAdminMode] = useState(false);
  const [targetUserId, setTargetUserId] = useState("");
  const [botLoading, setBotLoading] = useState(false);
  const [mode, setMode] = useState<"ANALYZE_SINGLE" | "EXPORT_ALL">(
    "ANALYZE_SINGLE",
  );
  const [taskName, setTaskName] = useState("Session 分析");
  const [remark, setRemark] = useState("");
  const [botId, setBotId] = useState("");
  const [botEnv, setBotEnv] = useState("");
  const [botSelectionKey, setBotSelectionKey] = useState("");
  const [stage, setStage] = useState<"all" | "draft" | "service">("all");
  const [sessionIdentifier, setSessionIdentifier] = useState("");
  const [question, setQuestion] = useState("");
  const [sessionLookbackDays, setSessionLookbackDays] = useState<number | null>(
    1,
  );
  const [llmAnalysis, setLlmAnalysis] = useState(true);
  const [llmUseDefault, setLlmUseDefault] = useState(true);
  const [llmModelChoice, setLlmModelChoice] = useState<string>(DEFAULT_EVOLVE_MODEL);
  const [customLlmModel, setCustomLlmModel] = useState("");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [createdId, setCreatedId] = useState("");

  const loadBots = async (ownerUserId?: string) => {
    setBotLoading(true);
    setError("");
    if (!ownerUserId) setBotId("");
    try {
      const { bots: items } = await api.sessionAnalysis.bots(ownerUserId);
      setBots(items);
      if (!ownerUserId) {
        setBotId("");
        setBotEnv("");
        setBotSelectionKey("");
      }
      if (!items.length)
        setError(`用户 ${ownerUserId || user?.userId || ""} 没有可用 Bot`);
    } catch (cause) {
      setBots([]);
      setError(cause instanceof Error ? cause.message : "Bot 列表加载失败");
    } finally {
      setBotLoading(false);
    }
  };

  useEffect(() => {
    if (user?.userId && !adminMode) void loadBots();
  }, [user?.userId, adminMode]);

  useEffect(() => {
    setTaskName(mode === "ANALYZE_SINGLE" ? "Session 分析" : "Session 导出");
  }, [mode]);

  if (createdId)
    return (
      <div className="mx-auto max-w-2xl px-4 py-16">
        <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm">
          <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-emerald-50 text-2xl text-emerald-600">
            ✓
          </span>
          <h1 className="mt-5 text-xl font-semibold text-gray-950">
            会话诊断任务已创建
          </h1>
          <p className="mt-2 text-sm text-gray-500">
            任务已提交至 AIS，可在任务详情中查看进度和结果。
          </p>
          <div className="mx-auto mt-5 max-w-md rounded-xl bg-gray-50 p-4 text-left text-sm">
            <p>
              <span className="text-gray-500">任务名称：</span>
              {taskName}
            </p>
            <p className="mt-2">
              <span className="text-gray-500">任务 ID：</span>
              <span className="font-mono text-xs">{createdId}</span>
            </p>
          </div>
          <button
            className={`${primaryButton} mt-6`}
            onClick={() => navigate(`/evolve/session-runs/${createdId}`)}
          >
            查看分析任务
          </button>
        </div>
      </div>
    );

  const create = async () => {
    setError("");
    if (!taskName.trim()) return setError("请输入任务名称");
    if (adminMode && !targetUserId.trim())
      return setError("请输入目标用户工号");
    if (!botId.trim())
      return setError(adminMode ? "请输入 Bot ID" : "请选择 Bot");
    if (mode === "ANALYZE_SINGLE" && !sessionIdentifier.trim())
      return setError("请输入 Session 标识");
    const llmModel =
      llmModelChoice === EVOLVE_CUSTOM_MODEL ? customLlmModel.trim() : llmModelChoice;
    if (mode === "ANALYZE_SINGLE" && llmAnalysis && !llmUseDefault && !llmModel)
      return setError("请输入自定义 LLM 模型名称");
    setBusy(true);
    try {
      const result = await api.sessionAnalysis.create({
        taskName: taskName.trim(),
        ...(remark.trim() ? { remark: remark.trim() } : {}),
        mode,
        botId,
        ...(!adminMode && botEnv ? { botEnv } : {}),
        ...(adminMode ? { targetUserId: targetUserId.trim() } : {}),
        stage,
        ...(mode === "ANALYZE_SINGLE"
          ? {
              sessionIdentifier: sessionIdentifier.trim(),
              ...(question.trim() ? { question: question.trim() } : {}),
              sessionLookbackDays,
              llmAnalysis,
              llmUseDefault,
              ...(llmAnalysis && !llmUseDefault
                ? {
                    llmModel,
                    ...(llmApiKey.trim()
                      ? { llmApiKey: llmApiKey.trim() }
                      : {}),
                  }
                : {}),
            }
          : {}),
      });
      setCreatedId(result.analysisId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl px-4 py-7 sm:px-6 lg:px-8">
      <button
        onClick={() => navigate("/evolve")}
        className="mb-5 text-sm text-gray-500 hover:text-gray-800"
      >
        ‹ 返回任务列表
      </button>
      <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-6 py-5">
          <p className="text-sm font-medium text-blue-600">会话诊断</p>
          <h1 className="mt-2 text-xl font-semibold text-gray-950">
            诊断 Session 运行过程
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            基于 NAS 原始 Session 文件调用 AIS 分析；多个 Session
            暂时只支持导出。
          </p>
        </div>
        <div className="p-6">
          <div className="grid gap-7 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
          <div className="space-y-6">
          <section>
            <h2 className="text-sm font-semibold text-gray-900">任务信息</h2>
            <div className="mt-3 grid gap-4 sm:grid-cols-2">
              <label className="text-xs font-medium text-gray-600">
                任务名称 <span className="text-red-500">*</span>
                <input
                  className={`${inputClass} mt-1.5`}
                  maxLength={128}
                  value={taskName}
                  onChange={(e) => setTaskName(e.target.value)}
                />
              </label>
              <label className="text-xs font-medium text-gray-600">
                备注
                <input
                  className={`${inputClass} mt-1.5`}
                  maxLength={1000}
                  value={remark}
                  onChange={(e) => setRemark(e.target.value)}
                  placeholder="可选"
                />
              </label>
            </div>
          </section>
          <section className="border-t border-gray-100 pt-6">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-gray-900">分析对象</h2>
              {user?.isClawEvolveAdmin && (
                <button
                  type="button"
                  className={
                    adminMode
                      ? "rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800"
                      : secondaryButton
                  }
                  onClick={() => {
                    setAdminMode((enabled) => !enabled);
                    setTargetUserId("");
                    setBots([]);
                    setBotId("");
                    setBotEnv("");
                    setBotSelectionKey("");
                  }}
                >
                  {adminMode ? "退出管理员" : "启用管理员"}
                </button>
              )}
            </div>
            {adminMode && (
              <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <input
                    className={inputClass}
                    value={targetUserId}
                    onChange={(e) => setTargetUserId(e.target.value)}
                    placeholder="目标用户工号（必填）"
                  />
                  <input
                    className={inputClass}
                    value={botId}
                    onChange={(e) => setBotId(e.target.value)}
                    placeholder="Bot ID（必填）"
                  />
                </div>
                <p className="mt-2 text-xs text-amber-700">
                  管理员输入将原样提交，不查询或校验 Bot 列表。
                </p>
              </div>
            )}
            <div className="mt-3">
              {!adminMode && (
                <div><span className="text-xs font-medium text-gray-600">Bot <span className="text-red-500">*</span></span><div className="mt-1.5"><EvolveBotPicker bots={bots} value={botSelectionKey} disabled={botLoading} emptyText={botLoading ? "正在加载…" : "当前没有可用 Bot"} onChange={(key, bot) => { setBotSelectionKey(key); setBotId(bot.botId); setBotEnv(bot.env || "") }} /></div></div>
              )}
              <div className="mt-4">
                <span className="text-xs font-medium text-gray-600">Session 来源</span>
                <p className="mt-1 text-xs leading-5 text-gray-500">会话诊断同时支持个人 Bot 和服务 Bot，也可以只分析其中一种来源。</p>
                <div role="radiogroup" aria-label="Session 来源" className="mt-3 grid gap-3 sm:grid-cols-3">
                  {([
                    ["all", "个人 + 服务 Bot", "分析两种运行形态，默认推荐"],
                    ["draft", "仅个人 Bot", "只分析个人使用产生的 Session"],
                    ["service", "仅服务 Bot", "只分析服务态产生的 Session"],
                  ] as const).map(([value, title, description]) => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={stage === value}
                      onClick={() => setStage(value)}
                      className={`rounded-xl border p-3.5 text-left transition ${stage === value ? "border-blue-500 bg-blue-50 ring-1 ring-blue-500/10" : "border-gray-200 bg-white hover:border-blue-200"}`}
                    >
                      <span className={`block text-sm font-semibold ${stage === value ? "text-blue-800" : "text-gray-800"}`}>{title}</span>
                      <span className="mt-1.5 block text-[11px] leading-4 text-gray-500">{description}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </section>
          <section className="border-t border-gray-100 pt-6">
            <h2 className="text-sm font-semibold text-gray-900">任务模式</h2>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              {(
                [
                  [
                    "ANALYZE_SINGLE",
                    "单 Session 分析",
                    "定位一个 Session 并调用大模型诊断",
                  ],
                  [
                    "EXPORT_ALL",
                    "多 Session 导出",
                    "打包所有 Session，不调用大模型",
                  ],
                ] as const
              ).map(([value, title, description]) => (
                <button
                  key={value}
                  onClick={() => setMode(value)}
                  className={`rounded-xl border p-4 text-left ${mode === value ? "border-blue-500 bg-blue-50" : "border-gray-200"}`}
                >
                  <p className="text-sm font-medium text-gray-900">{title}</p>
                  <p className="mt-1 text-xs text-gray-500">{description}</p>
                </button>
              ))}
            </div>
            {mode === "ANALYZE_SINGLE" && (
              <label className="mt-4 block text-xs font-medium text-gray-600">
                Session 时间范围
                <select
                  className={`${inputClass} mt-1.5`}
                  value={sessionLookbackDays ?? "all"}
                  onChange={(e) =>
                    setSessionLookbackDays(
                      e.target.value === "all" ? null : Number(e.target.value),
                    )
                  }
                >
                  <option value={1}>会话最近 1 天（默认）</option>
                  <option value={3}>会话最近 3 天</option>
                  <option value={7}>会话最近 7 天</option>
                  <option value={30}>会话最近 30 天</option>
                  <option value="all">全部会话时间</option>
                </select>
                <span className="mt-1 block text-[11px] font-normal text-gray-500">
                  以 Session 文件内最新事件时间为准向前截取。
                </span>
              </label>
            )}
            {mode === "ANALYZE_SINGLE" ? (
              <div className="mt-4 space-y-4">
                <label className="block text-xs font-medium text-gray-600">
                  Session 标识
                  <input
                    className={`${inputClass} mt-1.5`}
                    value={sessionIdentifier}
                    onChange={(e) => setSessionIdentifier(e.target.value)}
                    placeholder="输入 Session ID 或 Session Key"
                  />
                  <span className="mt-1 block text-[11px] font-normal text-gray-500">
                    系统会先按 Session ID 查找，未找到时再按 Session Key
                    查找其最新 Session。
                  </span>
                </label>
                <label className="block text-xs font-medium text-gray-600">
                  分析问题
                  <textarea
                    className={`${inputClass} mt-1.5 min-h-24 resize-y`}
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="可选：希望重点判断或诊断的问题"
                  />
                </label>
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                  <label className="flex cursor-pointer items-center justify-between gap-4">
                    <span>
                      <span className="block text-sm font-medium text-gray-900">
                        LLM 分析
                      </span>
                      <span className="mt-1 block text-xs text-gray-500">
                        默认开启；关闭后仅定位、解析和打包 Session。
                      </span>
                    </span>
                    <input
                      type="checkbox"
                      checked={llmAnalysis}
                      onChange={(e) => {
                        setLlmAnalysis(e.target.checked);
                        if (!e.target.checked) setLlmUseDefault(true);
                      }}
                      className="h-4 w-4 rounded border-gray-300 text-blue-600"
                    />
                  </label>
                  {llmAnalysis && (
                    <div className="mt-4 border-t border-gray-200 pt-4">
                      <label className="flex cursor-pointer items-start gap-2">
                        <input
                          type="checkbox"
                          checked={llmUseDefault}
                          onChange={(e) => setLlmUseDefault(e.target.checked)}
                          className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600"
                        />
                        <span>
                          <span className="block text-xs font-medium text-gray-700">
                            使用默认 LLM 配置
                          </span>
                          <span className="mt-0.5 block text-[11px] text-gray-500">
                            使用 AIS config.yaml 中的模型与
                            Token；默认模式不支持选择模型。
                          </span>
                        </span>
                      </label>
                      {!llmUseDefault && (
                        <div className="mt-4 grid gap-4 sm:grid-cols-2">
                          <EvolveModelFields
                            choice={llmModelChoice}
                            customValue={customLlmModel}
                            onChoiceChange={setLlmModelChoice}
                            onCustomValueChange={setCustomLlmModel}
                            selectAriaLabel="Session 分析模型"
                            customAriaLabel="Session 分析自定义模型名称"
                            inputClassName={inputClass}
                          />
                          <label className="text-xs font-medium text-gray-600 sm:col-span-2">
                            Token（可选）
                            <input
                              type="password"
                              autoComplete="new-password"
                              className={`${inputClass} mt-1.5`}
                              value={llmApiKey}
                              onChange={(e) => setLlmApiKey(e.target.value)}
                              placeholder="留空则继续使用 AIS 默认 Token"
                            />
                            <span className="mt-1 block text-[11px] font-normal text-amber-600">
                              Token 仅用于本次 AIS 调用，任务详情不会展示。
                            </span>
                          </label>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
                将导出该 Bot 有界目录内的所有合法 Session 文件。多个 Session
                暂不支持分析。
              </p>
            )}
          </section>
          {error && (
            <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
              {error}
            </p>
          )}
          </div>
          <EvolveTaskOverview
            label="诊断"
            subtitle={mode === "ANALYZE_SINGLE" ? "单 Session 分析" : "多 Session 导出"}
            stages={mode === "ANALYZE_SINGLE"
              ? [
                  ["定位 Session", "按 Session ID 或 Key 读取原始轨迹"],
                  ["会话诊断", "解析轨迹并按配置执行 LLM 分析"],
                ]
              : [["导出 Session", "打包 Bot 有界目录内的合法 Session"]]}
            deliverables={mode === "ANALYZE_SINGLE"
              ? [
                  ["原始轨迹", "定位后的 Session 证据"],
                  ["分析报告", "问题判断与关键证据"],
                  ["任务归档", "输入、输出与运行日志"],
                ]
              : [
                  ["Session 包", "多个原始 Session 文件"],
                  ["导出清单", "文件范围与归档信息"],
                ]}
          />
          </div>
        </div>
        <div className="flex justify-end gap-3 border-t border-gray-100 px-6 py-4">
          <button
            className={secondaryButton}
            onClick={() => navigate("/evolve")}
          >
            取消
          </button>
          <button
            disabled={busy}
            className={primaryButton}
            onClick={() => void create()}
          >
            {busy
              ? "提交中…"
              : mode === "ANALYZE_SINGLE"
                ? "创建分析任务"
                : "创建导出任务"}
          </button>
        </div>
      </div>
    </div>
  );
}

function SessionAnalysisDetail() {
  const { user } = useClientUser();
  const navigate = useNavigate();
  const location = useLocation();
  const id = decodeURIComponent(
    location.pathname.split("/").filter(Boolean).at(-1) ?? "",
  );
  const [task, setTask] = useState<SessionAnalysisTask | null>(null);
  const [error, setError] = useState("");
  const [reportCopied, setReportCopied] = useState(false);
  const [sharingBusy, setSharingBusy] = useState(false);
  const [selectedEventIndex, setSelectedEventIndex] = useState(0);
  const [eventQuery, setEventQuery] = useState("");
  const load = async () => {
    try {
      setTask(await api.sessionAnalysis.get(id));
      setError("");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "任务加载失败";
      setError(message.includes("TASK_NOT_SHARED")
        ? "权限不足，请联系任务 Owner 开启分享"
        : message);
    }
  };
  const copyReport = async () => {
    if (!task?.reportMarkdown) return;
    try {
      await navigator.clipboard.writeText(task.reportMarkdown);
      setReportCopied(true);
      window.setTimeout(() => setReportCopied(false), 1600);
    } catch {
      setError("复制失败，请手动选择报告内容");
    }
  };
  useEffect(() => {
    if (!id) return;
    void load();
  }, [id]);
  useEffect(() => {
    if (!task || ["completed", "failed", "canceled"].includes(task.status))
      return;
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [task?.status, id]);
  const download = async (name: string) => {
    if (!task) return;
    try {
      const { url, filename } = await api.sessionAnalysis.downloadUrl(
        task.analysisId,
        name,
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "下载失败");
    }
  };
  if (!task)
    return (
      <div className="mx-auto max-w-5xl px-4 py-20 text-center text-sm text-gray-500">
        {error || "正在加载任务详情…"}
      </div>
    );
  const d = diagnosis(task);
  const analysisResult = task.result?.analysis as
    | Record<string, unknown>
    | undefined;
  const evidenceTruncated = analysisResult?.evidenceTruncated === true;
  const sessionTimeWindow = analysisResult?.sessionTimeWindow as
    | Record<string, unknown>
    | undefined;
  const sessionTimeTruncated = sessionTimeWindow?.truncated === true;
  const sessionEvents = (task.sessionPreview?.events ??
    []) as BenchSessionEvent[];
  const activeEventIndex = Math.min(selectedEventIndex, Math.max(sessionEvents.length - 1, 0));
  const activeEvent = sessionEvents[activeEventIndex];
  const normalizedEventQuery = eventQuery.trim().toLowerCase();
  const visibleSessionEvents = sessionEvents
    .map((event, index) => ({ event, index }))
    .filter(({ event }) => !normalizedEventQuery || [
      eventLabel(event),
      eventRole(event),
      eventPreview(event),
    ].some((value) => value.toLowerCase().includes(normalizedEventQuery)));
  const status = taskStatus(task.status);
  const typeLabel =
    task.mode === "ANALYZE_SINGLE" ? "会话诊断" : "Session 导出";
  const sessionIdentity =
    task.sessionIdentifier ||
    task.sessionId ||
    task.sessionKey ||
    "全部 Session";
  const canShare = user?.userId === task.createdBy || user?.isClawEvolveAdmin === true;
  const canOperate = user?.userId === task.userId || canShare;
  const toggleSharing = async () => {
    if (sharingBusy) return;
    setSharingBusy(true);
    try {
      await api.evolve.setTaskShared(task.analysisId, !task.shared);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新分享设置失败");
    } finally {
      setSharingBusy(false);
    }
  };
  return (
    <div className="mx-auto w-full max-w-[1440px] px-4 py-7 sm:px-6 lg:px-8">
      <button
        onClick={() => navigate("/evolve")}
        className="mb-5 inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800"
      >
        ‹ 返回任务列表
      </button>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${status.className}`}
            >
              {status.label}
            </span>
            <span className="inline-flex rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">
              {typeLabel}
            </span>
            <span className="font-mono text-xs text-gray-400">
              {task.analysisId}
            </span>
          </div>
          <h1 className="mt-3 text-2xl font-semibold text-gray-950">
            {task.taskName || `${typeLabel}任务`}
          </h1>
          {task.remark && (
            <p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-gray-500">
              {task.remark}
            </p>
          )}
          <div className="mt-3 flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
              ▣
            </span>
            <div>
              <p className="text-sm font-medium text-gray-900">
                {task.botName || task.botId}
              </p>
              <p className="font-mono text-[11px] text-gray-400">
                {task.userId} / {task.botId}
              </p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {task.shared && <span className="rounded-full bg-green-50 px-3 py-1.5 text-xs font-medium text-green-700">已公开分享</span>}
          {canShare && <button type="button" disabled={sharingBusy} className={secondaryButton} onClick={() => void toggleSharing()}>{sharingBusy ? "更新中…" : task.shared ? "关闭分享" : "分享"}</button>}
        </div>
      </div>
      {error && (
        <p className="mt-5 rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}
      <div className="mt-6 grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(260px,300px)]">
        <main className="min-w-0 space-y-5">
          <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
            <header className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">
                  诊断工作流
                </h2>
                <p className="mt-1 text-xs text-gray-500">
                  定位 NAS 原始 Session，通过 AIS 完成诊断或导出。
                </p>
              </div>
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-medium ${status.className}`}
              >
                {status.label}
              </span>
            </header>
            <div className="p-6">
              <div className={`max-w-md rounded-2xl border p-5 ${status.node}`}>
                <div className="flex items-center justify-between">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-sm font-semibold text-white">
                    1
                  </span>
                  <span
                    className={`rounded-full px-2.5 py-1 text-xs font-medium ${status.className}`}
                  >
                    {status.label}
                  </span>
                </div>
                <h3 className="mt-4 font-semibold text-gray-900">
                  {task.mode === "ANALYZE_SINGLE"
                    ? "Session 定位与诊断"
                    : "Session 定位与导出"}
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  AIS · claw_realtime_analysis
                </p>
                <div className="mt-4 border-t border-gray-200/70 pt-3">
                  <p className="text-[10px] text-gray-400">交付物</p>
                  <p className="mt-1 text-xs text-gray-600">
                    {task.mode === "ANALYZE_SINGLE"
                      ? "Session 文件 · 诊断报告"
                      : "Session 压缩包"}
                  </p>
                </div>
              </div>
            </div>
          </section>
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">
                  执行记录
                </h2>
                <p className="mt-1 text-xs text-gray-500">
                  AIS 投递、运行标识、平台响应、输出和错误信息。
                </p>
              </div>
              <span className="text-xs text-gray-400">1 个 Step</span>
            </div>
            <div className="mt-5 rounded-xl border border-gray-200 p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <p className="text-sm font-semibold text-gray-900">
                      {task.mode === "ANALYZE_SINGLE"
                        ? "会话诊断"
                        : "Session 导出"}
                    </p>
                    <span
                      className={`rounded-full px-2 py-1 text-[10px] font-medium ${status.className}`}
                    >
                      {status.label}
                    </span>
                    <span className="rounded-full bg-sky-50 px-2 py-1 text-[10px] font-medium text-sky-700">
                      AIS executeSnapshot
                    </span>
                  </div>
                  <p className="mt-1 font-mono text-[10px] text-gray-400">
                    {task.stepId || "—"}
                  </p>
                </div>
                {task.aisJobUrl ? (
                  <a
                    className="text-xs font-medium text-blue-600 hover:underline"
                    href={task.aisJobUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    AIS Job {task.aisJobId} ↗
                  </a>
                ) : (
                  <span className="font-mono text-xs text-gray-400">
                    {task.aisJobId || "—"}
                  </span>
                )}
              </div>
              {task.summary && (
                <p className="mt-3 text-sm text-gray-700">{task.summary}</p>
              )}
              {task.error && (
                <div className="mt-3 rounded-lg bg-red-50 p-3">
                  <p className="text-xs font-medium text-red-800">
                    {task.errorCode ? `${task.errorCode}: ` : ""}失败原因
                  </p>
                  <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-red-700">
                    {task.error}
                  </p>
                </div>
              )}
              <div className="mt-3 grid gap-3 border-t border-gray-100 pt-3 text-[11px] sm:grid-cols-3">
                <div>
                  <p className="text-gray-400">创建时间</p>
                  <p className="mt-1 text-gray-700">
                    {formatTime(task.stepCreatedAt)}
                  </p>
                </div>
                <div>
                  <p className="text-gray-400">开始时间</p>
                  <p className="mt-1 text-gray-700">
                    {formatTime(task.stepStartedAt)}
                  </p>
                </div>
                <div>
                  <p className="text-gray-400">完成时间</p>
                  <p className="mt-1 text-gray-700">
                    {formatTime(task.stepCompletedAt)}
                  </p>
                </div>
              </div>
              <details className="mt-3 border-t border-gray-100 pt-3">
                <summary className="cursor-pointer text-xs font-medium text-blue-600">
                  查看命令与运行信息
                </summary>
                <div className="mt-3 grid gap-3 rounded-lg border border-gray-200 bg-gray-50 p-3 text-[10px] sm:grid-cols-2">
                  <div>
                    <p className="text-gray-400">AIS Job ID</p>
                    <p className="mt-1 break-all font-mono text-gray-700">
                      {task.aisJobId || "—"}
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-400">执行器</p>
                    <p className="mt-1 font-mono text-gray-700">
                      AIStudio / Snapshot 62310015
                    </p>
                  </div>
                  <div className="sm:col-span-2">
                    <p className="text-gray-400">Command</p>
                    <p className="mt-1 break-all font-mono leading-5 text-gray-700">
                      {task.stepCommand || "—"}
                    </p>
                  </div>
                </div>
                {task.stepResponse && (
                  <div className="mt-3">
                    <p className="mb-2 text-xs font-medium text-gray-600">
                      AIStudio 投递响应
                    </p>
                    <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-950 p-4 text-[10px] leading-5 text-gray-200">
                      {JSON.stringify(task.stepResponse, null, 2)}
                    </pre>
                  </div>
                )}
                {task.stepOutput && (
                  <div className="mt-3">
                    <p className="mb-2 text-xs font-medium text-gray-600">
                      Step 结构化输出
                    </p>
                    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-gray-950 p-4 text-[10px] leading-5 text-gray-200">
                      {JSON.stringify(task.stepOutput, null, 2)}
                    </pre>
                  </div>
                )}
              </details>
            </div>
            {task.status === "failed" && canOperate && (
              <button
                className={`${primaryButton} mt-4`}
                onClick={async () => {
                  try {
                    await api.sessionAnalysis.retry(task.analysisId);
                    await load();
                  } catch (cause) {
                    setError(
                      cause instanceof Error ? cause.message : "重试失败",
                    );
                  }
                }}
              >
                继续执行
              </button>
            )}
          </section>
          {task.mode === "EXPORT_ALL" && task.status === "completed" && (
            <p className="rounded-xl bg-green-50 p-4 text-sm text-green-800">
              导出完成。多个 Session 暂不支持分析，请下载 Session 压缩包。
            </p>
          )}
          {task.mode === "ANALYZE_SINGLE" &&
            task.status === "completed" &&
            sessionTimeTruncated && (
              <div className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-800">
                <p className="font-medium">Session 已按时间范围截取</p>
                <p className="mt-1 text-xs leading-5">
                  本次定位到完整 Session 后，仅使用其最新{" "}
                  {task.sessionLookbackDays ?? "全部"}{" "}
                  天范围内的内容进行解析和分析；下载的 Session
                  文件也是实际参与分析的截取结果。
                </p>
              </div>
            )}
          {task.mode === "ANALYZE_SINGLE" && task.status === "completed" && (
            <section className="space-y-4">
              {evidenceTruncated && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                  <p className="font-medium">⚠ Session 内容已截断</p>
                  <p className="mt-1 text-xs leading-5">
                    输入超过配置上限，本次结论仅基于 Session
                    文件尾部证据。请结合原始文件复核。
                  </p>
                </div>
              )}
              <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-xl border bg-white p-4">
                  <p className="text-xs text-gray-500">目标状态</p>
                  <p className="mt-1 font-semibold">{field(d.goalStatus)}</p>
                </div>
                <div className="rounded-xl border bg-white p-4">
                  <p className="text-xs text-gray-500">诊断结论</p>
                  <p className="mt-1 font-semibold">{field(d.verdict)}</p>
                </div>
                <div className="rounded-xl border bg-white p-4">
                  <p className="text-xs text-gray-500">主问题分类</p>
                  <p className="mt-1 font-mono text-sm">
                    {field(d.primaryCode)}
                  </p>
                </div>
              </div>
              <div className="rounded-xl border-l-4 border-red-400 bg-red-50 p-4">
                <p className="text-xs font-medium text-red-700">根因摘要</p>
                <p className="mt-1 text-gray-900">
                  {field(d.rootCauseSummary, "请查看完整诊断报告")}
                </p>
                {field(d.impactSummary, "") && (
                  <p className="mt-2 text-sm text-gray-600">
                    {field(d.impactSummary, "")}
                  </p>
                )}
              </div>
            </section>
          )}
          {task.mode === "ANALYZE_SINGLE" && task.sessionPreview && (
            <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
              <header className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-5 py-4">
                <div>
                  <h2 className="text-base font-semibold text-gray-900">
                    Session 事件
                  </h2>
                  <p className="mt-1 text-xs text-gray-500">
                    {task.sessionPreview.eventCount} 个事件
                    {task.sessionPreview.parseErrorCount
                      ? ` · ${task.sessionPreview.parseErrorCount} 行解析失败`
                      : ""}
                  </p>
                </div>
                <button
                  className={secondaryButton}
                  onClick={() => void download("raw")}
                >
                  下载 Session 文件
                </button>
              </header>
              <div className="grid min-h-[580px] lg:grid-cols-[300px_minmax(0,1fr)]">
                <aside className="border-b border-gray-200 bg-gray-50/70 lg:border-b-0 lg:border-r">
                  <div className="border-b border-gray-200 p-3">
                    <label className="relative block">
                      <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-gray-400">⌕</span>
                      <input
                        value={eventQuery}
                        onChange={(event) => setEventQuery(event.target.value)}
                        className="w-full rounded-lg border border-gray-200 bg-white py-2 pl-8 pr-3 text-xs text-gray-800 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
                        placeholder="搜索事件、角色或内容"
                      />
                    </label>
                  </div>
                  <div className="max-h-[620px] overflow-auto px-2 py-3">
                    {visibleSessionEvents.length ? (
                      <div className="relative space-y-1 before:absolute before:bottom-4 before:left-[22px] before:top-4 before:w-px before:bg-gray-200">
                        {visibleSessionEvents.map(({ event, index }) => {
                          const selected = activeEventIndex === index;
                          const preview = eventPreview(event).replace(/\s+/g, " ").slice(0, 80);
                          return (
                            <button
                              key={index}
                              type="button"
                              aria-current={selected ? "true" : undefined}
                              onClick={() => setSelectedEventIndex(index)}
                              className={`relative flex w-full gap-3 rounded-lg border px-2.5 py-2.5 text-left transition ${selected ? "border-blue-200 bg-blue-50 shadow-sm" : "border-transparent hover:border-gray-200 hover:bg-white"}`}
                            >
                              <span className={`relative z-10 mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border bg-white text-[9px] font-semibold ${selected ? "border-blue-500 text-blue-600" : "border-gray-300 text-gray-500"}`}>
                                {index + 1}
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="flex items-center justify-between gap-2">
                                  <span className={`truncate text-xs font-medium ${selected ? "text-blue-800" : "text-gray-800"}`}>{eventLabel(event)}</span>
                                  <span className="shrink-0 text-[10px] text-gray-400">{event.timestamp || ""}</span>
                                </span>
                                <span className="mt-1 block truncate text-[11px] text-gray-500">{eventRole(event)}{preview ? ` · ${preview}` : ""}</span>
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="px-3 py-10 text-center text-xs text-gray-400">没有匹配的事件</p>
                    )}
                    {task.sessionPreview.truncated && (
                      <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-700">
                        仅展示前 200 个事件，请下载 Session 文件查看全部内容。
                      </div>
                    )}
                  </div>
                </aside>
                <div className="min-w-0 bg-white">
                  {activeEvent ? (
                    <>
                      <div className="border-b border-gray-100 px-5 py-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-sm font-semibold text-gray-950">事件 {activeEventIndex + 1}</span>
                          <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-700">{eventLabel(activeEvent)}</span>
                          <span className="rounded bg-blue-50 px-2 py-0.5 font-mono text-xs text-blue-700">{eventRole(activeEvent)}</span>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-400">
                          {activeEvent.timestamp && <span>时间：{activeEvent.timestamp}</span>}
                          {eventUsageText(activeEvent) && <span>用量：{eventUsageText(activeEvent)}</span>}
                        </div>
                      </div>
                      <div className="max-h-[620px] space-y-4 overflow-auto p-5">
                        {eventContentBlocks(activeEvent).map((block, blockIndex) => (
                          <section
                            key={blockIndex}
                            className={`overflow-hidden rounded-xl border ${block.tone === "tool" ? "border-purple-100 bg-purple-50/60" : block.tone === "thinking" ? "border-amber-100 bg-amber-50/60" : "border-gray-200 bg-gray-50/60"}`}
                          >
                            <div className="border-b border-current/5 px-4 py-2 text-xs font-medium text-gray-600">{block.label}</div>
                            <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-xs leading-6 text-gray-800">{block.text}</pre>
                          </section>
                        ))}
                        <details className="rounded-xl border border-gray-200 bg-white">
                          <summary className="cursor-pointer px-4 py-3 text-xs font-medium text-gray-500 hover:text-gray-700">原始事件 JSON</summary>
                          <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t border-gray-200 bg-gray-950 p-4 text-xs leading-5 text-gray-100">{JSON.stringify(activeEvent, null, 2)}</pre>
                        </details>
                      </div>
                    </>
                  ) : (
                    <div className="flex min-h-[420px] items-center justify-center text-sm text-gray-400">暂无可展示事件</div>
                  )}
                </div>
              </div>
            </section>
          )}
          {task.reportMarkdown && (
            <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
              <header className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
                <div className="flex items-center gap-2">
                  <h2 className="text-base font-semibold text-gray-900">完整诊断报告</h2>
                  <span className="rounded bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-600">Markdown</span>
                </div>
                <button
                  className={secondaryButton}
                  onClick={() => void copyReport()}
                >
                  {reportCopied ? "已复制" : "复制 Markdown"}
                </button>
              </header>
              <div className="bg-slate-50/70 px-4 py-6 sm:px-8 sm:py-8">
                <article className="mx-auto max-w-4xl overflow-x-auto rounded-2xl border border-gray-200 bg-white px-6 py-8 shadow-sm sm:px-10">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={reportMarkdownComponents} skipHtml>
                    {normalizeDiagnosisMarkdown(task.reportMarkdown)}
                  </ReactMarkdown>
                </article>
              </div>
            </section>
          )}
          {task.status === "completed" && task.artifacts && (
            <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
              <h2 className="mb-3 text-base font-semibold">任务产物</h2>
              <div className="flex flex-wrap gap-2">
                {task.artifacts
                  .filter(
                    (name) =>
                      !["result", "manifest"].includes(name) &&
                      !(task.mode === "ANALYZE_SINGLE" && name === "raw"),
                  )
                  .map((name) => (
                    <button
                      key={name}
                      onClick={() => void download(name)}
                      className={secondaryButton}
                    >
                      {artifactLabel(name, task.mode)}
                    </button>
                  ))}
              </div>
            </section>
          )}
        </main>
        <aside className="min-w-0 space-y-4">
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900">任务信息</h2>
            <dl className="mt-4 space-y-2">
              <DetailRow label="user_id" value={task.userId} mono />
              <DetailRow label="bot_id" value={task.botId} mono />
              <DetailRow label="任务类型" value={typeLabel} />
              <DetailRow label="任务名称" value={task.taskName || "—"} />
              <DetailRow label="备注" value={task.remark || "—"} />
              <DetailRow label="Step 数" value="1" />
              <DetailRow label="发起人" value={task.createdBy} mono />
            </dl>
          </section>
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-gray-900">任务配置</h2>
            <dl className="mt-4 divide-y divide-gray-100">
              <DetailRow
                label="Session 来源"
                value={
                  task.stage === "all"
                    ? "个人 + 服务 Bot"
                    : task.stage === "draft"
                      ? "仅个人 Bot"
                      : "仅服务 Bot"
                }
              />
              <DetailRow label="执行引擎" value={task.engineType} />
              <DetailRow label="Session 标识" value={sessionIdentity} mono />
              <DetailRow
                label="任务模式"
                value={
                  task.mode === "ANALYZE_SINGLE"
                    ? "单 Session 分析"
                    : "多 Session 导出"
                }
              />
              {task.mode === "ANALYZE_SINGLE" && (
                <>
                  <DetailRow
                    label="LLM 分析"
                    value={task.llmAnalysis ? "已启用" : "未启用"}
                  />
                  {task.llmAnalysis && (
                    <DetailRow
                      label="LLM 配置"
                      value={
                        task.llmUseDefault
                          ? "默认配置"
                          : task.llmModel || "自定义配置"
                      }
                    />
                  )}
                </>
              )}
            </dl>
            {task.question && (
              <div className="mt-4 border-t border-gray-100 pt-4">
                <p className="text-xs text-gray-500">分析问题</p>
                <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-gray-700">
                  {task.question}
                </p>
              </div>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}

export default function SessionAnalysis({
  view,
}: {
  view: "create" | "detail";
}) {
  return view === "create" ? (
    <CreateSessionAnalysis />
  ) : (
    <SessionAnalysisDetail />
  );
}
