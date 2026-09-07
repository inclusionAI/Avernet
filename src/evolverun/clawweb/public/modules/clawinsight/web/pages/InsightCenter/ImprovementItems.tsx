import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useClientUser } from "@avernet/clawweb-shared/web/hooks/useClientUser";
import { api } from "@avernet/clawweb-shared/web/api/client";
import { insightApi } from "../../api/insight";
import type { TCLogBot } from "@avernet/clawweb-shared/web/types";
import type { AutoRepairGrantView, FailureTaskIndex, ImprovementDetail, ImprovementView } from "../../types/insight";
import { InsightIcon, EmptyPanel, ErrorPanel, LoadingPanel } from "./InsightUi";
import { createRequestId, failureClassText, formatDateTime } from "./utils";
import FailureTaskDrawer from "./FailureTaskDrawer";
import { AdminExecuteDialog } from "./AdminReviewQueue";

type BotOption = { botId: string; botName: string; ownerUserId?: string };
type WorkView = "todo" | "processing" | "resolved" | "archived";

type Props = {
  ownerUserId?: string;
  readOnly?: boolean;
  botId?: string;
  selectedImprovementId?: number;
  botOptions: BotOption[];
  onBotChange: (botId?: string) => void;
  onSelectImprovement: (improvementId?: number) => void;
  onGoFailures: () => void;
};

const workViews: Array<{ value: WorkView; label: string }> = [
  { value: "todo", label: "待我处理" },
  { value: "processing", label: "处理中" },
  { value: "resolved", label: "已完成" },
  { value: "archived", label: "已驳回" },
];

const statusByWorkView: Record<WorkView, string> = {
  todo: "ACTIVE",
  processing: "IN_PROGRESS",
  resolved: "RESOLVED",
  archived: "ARCHIVED",
};

const rejectReasons = [
  ["EXPECTED_BUSINESS_FAILURE", "这是业务预期结果"],
  ["ALREADY_FIXED", "已经通过其他方式修复"],
  ["NOT_OWNER_SCOPE", "不属于我的处理范围"],
  ["NO_ACTION_NEEDED", "这个问题不影响业务"],
  ["MISIDENTIFIED", "错误识别或证据不准确"],
  ["OTHER", "其他原因"],
] as const;

function normalizedStatus(
  status: string,
): "ACTIVE" | "IN_PROGRESS" | "RESOLVED" | "ARCHIVED" {
  const value = String(status || "ACTIVE").toUpperCase();
  if (value === "RESOLVED") return "RESOLVED";
  if (value === "ARCHIVED") return "ARCHIVED";
  if (value === "IN_PROGRESS") return "IN_PROGRESS";
  return "ACTIVE";
}

function statusMeta(
  status: string,
  verificationStatus?: ImprovementView["verificationStatus"],
) {
  switch (normalizedStatus(status)) {
    case "IN_PROGRESS":
      if (verificationStatus === "PENDING") {
        return {
          label: "自动验收中",
          className: "bg-cyan-50 text-cyan-700",
        };
      }
      if (verificationStatus === "INSUFFICIENT_DATA") {
        return {
          label: "等待运行数据",
          className: "bg-cyan-50 text-cyan-700",
        };
      }
      if (verificationStatus === "STILL_PRESENT") {
        return {
          label: "验收未通过",
          className: "bg-red-50 text-red-700",
        };
      }
      return {
        label: "修复中",
        className: "bg-blue-50 text-blue-700",
      };
    case "RESOLVED":
      return {
        label: "已验收通过",
        className: "bg-emerald-50 text-emerald-700",
      };
    case "ARCHIVED":
      return {
        label: "已驳回",
        className: "bg-gray-100 text-gray-500",
      };
    default:
      if (verificationStatus === "STILL_PRESENT") {
        return {
          label: "自动修复未生效",
          className: "bg-red-50 text-red-700",
        };
      }
      return {
        label: "待修复",
        className: "bg-amber-50 text-amber-700",
      };
  }
}

function verificationText(item: ImprovementView): string {
  switch (item.verificationStatus) {
    case "VERIFIED":
      return `Agent 已确认问题消失 · 检查了 ${item.verificationNewSessionCount} 个新 Session`;
    case "STILL_PRESENT":
      return `Agent 验收后发现问题仍然出现，请继续修复。`;
    case "INSUFFICIENT_DATA":
      return "暂时没有足够的新 Session，Agent 会继续等待运行数据。";
    case "PENDING":
      return "Agent 会检查后续新会话，确认问题是否真正消失；你暂时不需要操作。";
    default:
      return "修复完成后标记已修复，系统会进入 Agent 自动验收。";
  }
}

function isAutoRepair(item: ImprovementView): boolean {
  return item.actionType === "DIRECT_EVOLUTION";
}

function repairCreatePath(item: ImprovementView): string {
  return `/evolve/new?type=repair&improvementId=${encodeURIComponent(item.improvementId)}`;
}

function isVerificationStage(item: ImprovementView): boolean {
  return normalizedStatus(item.status) === "IN_PROGRESS"
    && ["PENDING", "INSUFFICIENT_DATA"].includes(item.verificationStatus);
}

function canRerunRepair(item: ImprovementView): boolean {
  return normalizedStatus(item.status) === "IN_PROGRESS"
    && (item.verificationStatus === "STILL_PRESENT"
      || item.verificationStatus === "INSUFFICIENT_DATA"
      || ["failed", "canceled", "cancelled"].includes(String(item.latestEvolveTaskStatus ?? "").toLowerCase()));
}

function repairActionHint(item: ImprovementView): string {
  if (isVerificationStage(item)) return verificationText(item);
  if (
    ["ACTIVE", "IN_PROGRESS"].includes(normalizedStatus(item.status))
    && item.verificationStatus === "STILL_PRESENT"
    && !isAutoRepair(item)
  ) {
    return "自动修复未生效，请手动修复；完成后标记已修复，系统会再次验收。";
  }
  if (normalizedStatus(item.status) === "ACTIVE" && isAutoRepair(item)) {
    return "可选择自动修复进入进化室；如果你已经自行修复，也可以直接标记并进入 Agent 验收。";
  }
  if (normalizedStatus(item.status) === "ACTIVE") {
    return "该问题需要你手动修改 Agent 配置或业务设置。";
  }
  if (normalizedStatus(item.status) === "IN_PROGRESS" && isAutoRepair(item)) {
    return "Agent 正在进化室执行修复；执行完成后会自动进入验收。";
  }
  if (normalizedStatus(item.status) === "IN_PROGRESS") {
    return "完成修改后请标记已修复；系统会进入 Agent 自动验收，不会立即关单。";
  }
  if (normalizedStatus(item.status) === "RESOLVED") return "Agent 已完成验收，确认问题不再出现。";
  return "该改进项已驳回，Governance Agent 会结合原因减少同类问题再次打扰。";
}

export default function ImprovementItems({
  ownerUserId,
  readOnly = false,
  botId,
  selectedImprovementId,
  botOptions,
  onBotChange,
  onSelectImprovement,
  onGoFailures,
}: Props) {
  const navigate = useNavigate();
  const { user } = useClientUser();
  const [workView, setWorkView] = useState<WorkView>("todo");
  const [items, setItems] = useState<ImprovementView[]>([]);
  const [statusCounts, setStatusCounts] = useState({
    active: 0,
    inProgress: 0,
    resolved: 0,
    archived: 0,
  });
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pageCursors, setPageCursors] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const [detail, setDetail] = useState<ImprovementDetail | null>(null);
  const [loadedDetailId, setLoadedDetailId] = useState<number | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [statusSavingId, setStatusSavingId] = useState<number | null>(null);
  const [statusError, setStatusError] = useState("");
  const [handoffMessage, setHandoffMessage] = useState("");
  const [failureTask, setFailureTask] = useState<FailureTaskIndex | null>(null);
  const [rejecting, setRejecting] = useState<ImprovementView | null>(null);
  const [autoRepairing, setAutoRepairing] = useState<ImprovementDetail | null>(null);
  const [adminExecuting, setAdminExecuting] = useState<ImprovementDetail | null>(null);
  const [grantManagerVisible, setGrantManagerVisible] = useState(false);
  const adminListMode = Boolean(readOnly && user?.isAdmin);

  const loadImprovementPage = useCallback((cursor?: string) => adminListMode
    ? insightApi.adminImprovements({
        ownerUserId,
        botId,
        status: statusByWorkView[workView],
        includeAll: true,
        cursor,
        pageSize: 20,
      })
    : insightApi.improvements({
        ownerUserId,
        botId,
        status: statusByWorkView[workView],
        cursor,
        pageSize: 20,
      }), [adminListMode, ownerUserId, botId, workView]);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      setLoading(true);
      setError("");
      setPageCursors([null]);
      setPageIndex(0);
      loadImprovementPage()
        .then((result) => {
          if (active) {
            setItems(result.items);
            setNextCursor(result.nextCursor);
            setStatusCounts(result.statusCounts);
          }
        })
        .catch((reason) => {
          if (active)
            setError(reason instanceof Error ? reason.message : "改进项加载失败");
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    });
    return () => {
      active = false;
    };
  }, [loadImprovementPage, reloadKey]);

  useEffect(() => {
    let active = true;
    queueMicrotask(() => {
      if (!active) return;
      if (!selectedImprovementId) {
        setLoadedDetailId(null);
        setDetail(null);
        return;
      }
      setDetailLoading(true);
      setDetailError("");
      setStatusError("");
      const detailRequest = readOnly
        ? insightApi.adminImprovement(selectedImprovementId)
        : insightApi.improvement(selectedImprovementId);
      detailRequest
        .then((result) => {
          if (!active) return;
          setDetail(result);
          setLoadedDetailId(selectedImprovementId);
        })
        .catch((reason) => {
          if (active) {
            setDetailError(
              reason instanceof Error ? reason.message : "改进项详情加载失败",
            );
            setLoadedDetailId(selectedImprovementId);
          }
        })
        .finally(() => {
          if (active) setDetailLoading(false);
        });
    });
    return () => {
      active = false;
    };
  }, [readOnly, selectedImprovementId, reloadKey]);

  useEffect(() => {
    let active = true;
    let frame: number | null = null;
    queueMicrotask(() => {
      if (!active) return;
      if (!selectedImprovementId) {
        setDrawerVisible(false);
        return;
      }
      setDrawerVisible(false);
      frame = requestAnimationFrame(() => {
        if (active) setDrawerVisible(true);
      });
    });
    return () => {
      active = false;
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [selectedImprovementId]);

  const closeDrawer = () => {
    setDrawerVisible(false);
    setHandoffMessage("");
    window.setTimeout(() => onSelectImprovement(undefined), 200);
  };

  const currentUserId = user?.userId ?? "";
  const adminOperate = Boolean(readOnly && user?.isAdmin && ownerUserId);
  const canOperate = !readOnly || adminOperate;
  const activeDetail =
    selectedImprovementId && loadedDetailId === selectedImprovementId
      ? detail
      : null;
  const activeDetailLoading =
    Boolean(selectedImprovementId) &&
    (detailLoading || loadedDetailId !== selectedImprovementId);
  const activeDetailVerifying = activeDetail ? isVerificationStage(activeDetail) : false;
  const botNames = new Map(botOptions.map((bot) => [bot.botId, bot.botName]));

  const openFailureTask = (improvement: ImprovementDetail, evidence: ImprovementDetail["evidence"][number]) => {
    const task: FailureTaskIndex = {
      sourceDt: "",
      ownerUserId: improvement.botOwnerUserId,
      botId: improvement.botId,
      botName: botNames.get(improvement.botId) || improvement.botId,
      sessionId: evidence.sessionId,
      taskIndex: evidence.taskIndex,
      taskDescription: evidence.taskDescription,
      isComplete: 0,
      failureClass: evidence.failureClass,
      judgeReasonSummary: evidence.reasoningSummary,
      sessionStartTime: null,
      sessionEndTime: null,
      sessionDurationSeconds: null,
      isCron: false,
      dataAsOf: improvement.dataAsOf,
    };
    setDrawerVisible(false);
    window.setTimeout(() => {
      onSelectImprovement(undefined);
      setFailureTask(task);
    }, 200);
  };

  const visibleItems = items;

  const countForView = (view: WorkView) => {
    if (view === "processing") return statusCounts.inProgress;
    if (view === "resolved") return statusCounts.resolved;
    if (view === "archived") return statusCounts.archived;
    return statusCounts.active;
  };

  const replaceImprovement = (updated: ImprovementView) => {
    setItems((current) =>
      current.map((item) =>
        item.improvementId === updated.improvementId ? updated : item,
      ),
    );
    setDetail((current) =>
      current && current.improvementId === updated.improvementId
        ? { ...current, ...updated }
        : current,
    );
  };

  const changeStatus = async (
    improvement: ImprovementView,
    status: "ACTIVE" | "IN_PROGRESS" | "RESOLVED" | "ARCHIVED",
  ) => {
    if (statusSavingId) return;
    if (
      status === "RESOLVED" &&
      !window.confirm("确认将这个改进项标记为已完成？")
    ) {
      return;
    }
    setStatusSavingId(improvement.improvementId);
    setStatusError("");
    try {
      const updated = adminOperate && status === "ACTIVE" && normalizedStatus(improvement.status) === "ARCHIVED"
        ? await insightApi.adminReopenImprovement(improvement.improvementId, {
            version: improvement.version,
            reason: "管理员重新评估后决定继续处理该改进项。",
          })
        : await insightApi.updateImprovement(
            improvement.improvementId,
            { status, version: improvement.version },
          );
      replaceImprovement(updated);
      setWorkView(
        status === "ARCHIVED"
          ? "archived"
          : status === "RESOLVED"
            ? "resolved"
            : status === "IN_PROGRESS"
              ? "processing"
              : "todo",
      );
    } catch (reason) {
      setStatusError(
        reason instanceof Error
          ? reason.message
          : status === "ARCHIVED"
            ? "驳回失败"
            : status === "RESOLVED"
              ? "标记已完成失败"
              : status === "IN_PROGRESS"
                ? "开始处理失败"
                : "恢复失败",
      );
    } finally {
      setStatusSavingId(null);
    }
  };

  const markHandled = async (improvement: ImprovementView) => {
    if (statusSavingId) return;
    setStatusSavingId(improvement.improvementId);
    setStatusError("");
    setHandoffMessage("");
    try {
      const updated = adminOperate
        ? await insightApi.adminMarkHandled(improvement.improvementId, improvement.version)
        : await insightApi.markHandled(improvement.improvementId, improvement.version);
      replaceImprovement(updated);
      setHandoffMessage("已标记为修复完成，改进项已进入 Agent 自动验收；你暂时不需要继续操作。");
      setWorkView("processing");
    } catch (reason) {
      setStatusError(reason instanceof Error ? reason.message : "状态更新失败");
    } finally {
      setStatusSavingId(null);
    }
  };

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-gray-100 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-900">改进项</h2>
            <p className="mt-1 text-xs text-gray-400">
              {readOnly
                ? adminOperate
                  ? `管理员视角查看${ownerUserId === "*" ? "全部用户" : ownerUserId || "当前范围"}的待办，可代用户发起一次修复、推进验收或驳回。持续授权仍需 Owner 本人确认。`
                  : `管理员只读查看${ownerUserId === "*" ? "全部用户" : ownerUserId || "当前范围"}的改进项与状态。`
                : "Governance Agent 已判断修复方式；按提示授权自动修复或完成手动调整。"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {!readOnly && <button
              type="button"
              onClick={() => setGrantManagerVisible(true)}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs font-medium text-gray-600 hover:bg-gray-50"
            >
              <InsightIcon name="edit" />
              管理自动修复授权
            </button>}
            <select
              value={botId ?? ""}
              onChange={(event) => onBotChange(event.target.value || undefined)}
              className="min-w-56 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs outline-none focus:border-blue-500"
            >
              <option value="">{ownerUserId === "*" ? "全部用户的全部 Bot" : ownerUserId ? `${ownerUserId} 的全部 Bot` : "全部 Bot"}</option>
              {botOptions.map((bot) => (
                <option key={`${bot.ownerUserId ?? "mine"}:${bot.botId}`} value={bot.botId}>
                  {bot.botName}{ownerUserId === "*" && bot.ownerUserId ? ` · ${bot.ownerUserId}` : ""}
                </option>
              ))}
            </select>
            <button
              onClick={() => setReloadKey((value) => value + 1)}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 hover:bg-gray-50"
            >
              <InsightIcon name="refresh" />
              刷新
            </button>
          </div>
        </div>
        <div className="flex gap-6 overflow-x-auto px-5">
          {workViews.map((view) => {
            const active = workView === view.value;
            const count = countForView(view.value);
            return (
              <button
                key={view.value}
                onClick={() => {
                  setWorkView(view.value);
                  onSelectImprovement(undefined);
                }}
                className={`whitespace-nowrap border-b-2 py-3 text-xs font-medium transition ${active ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-800"}`}
              >
                {view.label}
                <span
                  className={`ml-1.5 rounded-full px-1.5 py-0.5 text-[10px] ${active ? "bg-blue-50 text-blue-700" : "bg-gray-100 text-gray-500"}`}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
        {statusError && (
          <p className="border-b border-red-100 bg-red-50 px-5 py-3 text-xs text-red-600">
            {statusError}
          </p>
        )}
        {loading ? (
          <LoadingPanel text="正在读取改进项…" />
        ) : error ? (
          <ErrorPanel
            message={error}
            onRetry={() => setReloadKey((value) => value + 1)}
          />
        ) : visibleItems.length === 0 ? (
          <EmptyPanel
            title={
              workView === "todo" ? "暂无待处理的改进项" : "当前视图暂无改进项"
            }
            description={
              workView === "todo"
                ? "可以从失败任务中选择同一 Bot 的任务创建改进项。"
                : undefined
            }
            action={
              workView === "todo" ? (
                <button
                  onClick={onGoFailures}
                  className="rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-medium text-white hover:bg-blue-700"
                >
                  去查看失败任务
                </button>
              ) : undefined
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1120px] table-fixed text-left">
              <thead className="border-b border-gray-100 bg-gray-50/80 text-[11px] font-medium text-gray-500">
                <tr>
                  <th className="w-[25%] px-5 py-3">改进项</th>
                  <th className="w-[15%] px-4 py-3">Bot</th>
                  <th className="w-[14%] px-4 py-3">问题规模</th>
                  <th className="w-[14%] px-4 py-3">当前进展</th>
                  <th className="w-[10%] px-4 py-3">{readOnly ? "处理用户" : "发起人"}</th>
                  <th className="w-[11%] px-4 py-3">更新时间</th>
                  <th className="w-[17%] px-4 py-3 text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {visibleItems.map((item) => {
                  const meta = statusMeta(item.status, item.verificationStatus);
                  return (
                    <tr
                      key={item.improvementId}
                      onClick={() => onSelectImprovement(item.improvementId)}
                      className="cursor-pointer text-xs transition hover:bg-blue-50/40"
                    >
                      <td className="px-5 py-4">
                        <p className="truncate text-sm font-medium text-gray-900">
                          {item.title}
                        </p>
                        {(item.rootCauseSummary || item.userGuidance) && (
                          <p className="mt-1.5 line-clamp-1 text-[11px] text-gray-500">
                            {item.rootCauseSummary || item.userGuidance}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-4">
                        <p className="truncate font-medium text-gray-700">
                          {botNames.get(item.botId) || item.botId}
                        </p>
                        <p className="mt-1 truncate font-mono text-[10px] text-gray-400">
                          {item.botId}
                        </p>
                      </td>
                      <td className="px-4 py-4 text-gray-600">
                        <p>{item.evidenceCount} 个失败任务</p>
                        <p className="mt-1 text-[10px] text-gray-400">
                          {item.sessionCount} 个 Session
                        </p>
                      </td>
                      <td className="px-4 py-4">
                        <span
                          className={`inline-flex rounded-full px-2.5 py-1 text-[10px] font-medium ${meta.className}`}
                        >
                          {meta.label}
                        </span>
                      </td>
                      <td className="px-4 py-4">
                        <p className="truncate text-gray-600">
                          {readOnly
                            ? item.ownerUserId
                            : item.createdBy === currentUserId
                              ? "我"
                              : item.createdBy}
                        </p>
                      </td>
                      <td className="px-4 py-4 text-[11px] text-gray-500">
                        {formatDateTime(item.gmtModified)}
                      </td>
                      <td
                        className="px-4 py-4"
                        onClick={(event) => event.stopPropagation()}
                      >
                        <div className="flex items-center justify-end gap-3 whitespace-nowrap">
                          <button
                            onClick={() => onSelectImprovement(item.improvementId)}
                            className="rounded-lg bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-700"
                          >
                            {readOnly ? "查看详情" : normalizedStatus(item.status) === "RESOLVED" ? "查看结果" : "查看并处理"}
                          </button>
                          {adminOperate && normalizedStatus(item.status) === "ARCHIVED" ? (
                            <button
                              disabled={statusSavingId === item.improvementId}
                              onClick={() => changeStatus(item, "ACTIVE")}
                              className="rounded-lg bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                            >
                              {statusSavingId === item.improvementId ? "恢复中…" : "恢复处理"}
                            </button>
                          ) : !readOnly && (normalizedStatus(item.status) === "ARCHIVED" ? (
                            <button
                              disabled={statusSavingId === item.improvementId}
                              onClick={() => changeStatus(item, "ACTIVE")}
                              className="font-medium text-blue-600 hover:text-blue-800 disabled:opacity-50"
                            >
                              {statusSavingId === item.improvementId
                                ? "恢复中…"
                                : "恢复处理"}
                            </button>
                          ) : normalizedStatus(item.status) !== "RESOLVED" ? (
                            <button
                              disabled={statusSavingId === item.improvementId}
                              onClick={() => setRejecting(item)}
                              className="rounded-lg bg-red-600 px-3 py-1.5 font-medium text-white hover:bg-red-700 disabled:opacity-50"
                            >
                              驳回
                            </button>
                          ) : null)}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {(pageIndex > 0 || nextCursor) && !loading && (
          <div className="flex items-center justify-center gap-3 border-t border-gray-100 p-3">
            <button
              disabled={loadingMore || pageIndex === 0}
              onClick={async () => {
                const targetIndex = pageIndex - 1;
                setLoadingMore(true);
                try {
                  const result = await loadImprovementPage(pageCursors[targetIndex] ?? undefined);
                  setItems(result.items);
                  setNextCursor(result.nextCursor);
                  setPageIndex(targetIndex);
                } finally {
                  setLoadingMore(false);
                }
              }}
              className="rounded-lg border border-gray-200 px-4 py-2 text-xs font-medium text-gray-600 disabled:opacity-40"
            >
              上一页
            </button>
            <span className="text-xs text-gray-400">第 {pageIndex + 1} 页 · 每页最多 20 条</span>
            <button
              disabled={loadingMore || !nextCursor}
              onClick={async () => {
                if (!nextCursor) return;
                setLoadingMore(true);
                try {
                  const result = await loadImprovementPage(nextCursor);
                  const targetIndex = pageIndex + 1;
                  setPageCursors((current) => [...current.slice(0, targetIndex), nextCursor]);
                  setItems(result.items);
                  setNextCursor(result.nextCursor);
                  setPageIndex(targetIndex);
                } finally {
                  setLoadingMore(false);
                }
              }}
              className="rounded-lg border border-gray-200 px-4 py-2 text-xs font-medium text-gray-600 disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        )}
      </section>

      {selectedImprovementId && (
        <div
          className={`fixed inset-0 z-[60] bg-gray-950/25 backdrop-blur-[1px] transition-opacity duration-200 ${drawerVisible ? "opacity-100" : "opacity-0"}`}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDrawer();
          }}
        >
          <aside
            className={`absolute inset-y-0 right-0 flex w-full max-w-3xl transform flex-col bg-gray-50 shadow-2xl transition-transform duration-200 ease-out ${drawerVisible ? "translate-x-0" : "translate-x-full"}`}
          >
            <div className="flex items-center justify-between border-b border-gray-200 bg-white px-5 py-4">
              <div>
                <p className="text-xs font-medium text-blue-600">改进项详情</p>
                <p className="mt-1 font-mono text-[10px] text-gray-400">
                  {selectedImprovementId}
                </p>
              </div>
              <button
                onClick={closeDrawer}
                className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              >
                <InsightIcon name="close" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">
              {activeDetailLoading ? (
                <LoadingPanel text="正在读取改进项详情…" />
              ) : detailError ? (
                <ErrorPanel
                  message={detailError}
                  onRetry={() => setReloadKey((value) => value + 1)}
                />
              ) : activeDetail ? (
                <div className="space-y-5 p-5 sm:p-6">
                  {statusError && (
                    <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs leading-5 text-red-700">
                      {statusError}
                    </p>
                  )}
                  {handoffMessage && (
                    <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-xs leading-5 text-emerald-800">
                      {handoffMessage}
                    </p>
                  )}
                  {readOnly && (
                    <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                      {adminOperate
                        ? "当前为管理员操作视角。你可以代用户发起一次修复、推进到 Agent 验收或驳回；持续授权仍需 Owner 本人确认。"
                        : "当前为管理员只读视角，仅查看对应用户的处理进度和 Agent 验收结果。"}
                    </p>
                  )}
                  <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span
                          className={`rounded-full px-2.5 py-1 text-[10px] font-medium ${statusMeta(activeDetail.status, activeDetail.verificationStatus).className}`}
                        >
                          {statusMeta(activeDetail.status, activeDetail.verificationStatus).label}
                        </span>
                        <span className="text-[11px] text-gray-400">
                          {activeDetail.evidenceCount} 个失败任务
                        </span>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${isAutoRepair(activeDetail) ? "bg-indigo-50 text-indigo-700" : "bg-orange-50 text-orange-700"}`}>
                          {isAutoRepair(activeDetail) ? "自动修复" : "手动修复"}
                        </span>
                      </div>
                      <h2 className="mt-3 break-words text-lg font-semibold leading-7 text-gray-950">
                        {activeDetail.title}
                      </h2>
                      <p className="mt-1 text-xs text-gray-500">
                        {botNames.get(activeDetail.botId) ||
                          activeDetail.botId}
                      </p>
                    </div>
                    <div className="mt-5 border-t border-gray-100 pt-4">
                      <div className="grid gap-3 lg:grid-cols-[auto_minmax(0,1fr)] lg:items-center">
                        <div className="flex flex-wrap items-center justify-start gap-2">
                          {canOperate && normalizedStatus(activeDetail.status) === "ACTIVE" && isAutoRepair(activeDetail) && (
                            <>
                              <button
                                onClick={() => adminOperate ? setAdminExecuting(activeDetail) : setAutoRepairing(activeDetail)}
                                className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition hover:bg-blue-700"
                              >
                                <InsightIcon name="bot" />
                                {adminOperate ? "管理员代处理一次" : "自动修复"}
                              </button>
                              {!adminOperate && (
                                <button
                                  disabled={statusSavingId === activeDetail.improvementId}
                                  onClick={() => markHandled(activeDetail)}
                                  className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-white px-4 py-2.5 text-xs font-semibold text-blue-700 shadow-sm transition hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  <InsightIcon name="check" />
                                  {statusSavingId === activeDetail.improvementId ? "正在更新…" : "我已手动修复，开始验收"}
                                </button>
                              )}
                            </>
                          )}
                          {canOperate && normalizedStatus(activeDetail.status) === "ACTIVE" && !isAutoRepair(activeDetail) && (
                            <button
                              disabled={statusSavingId === activeDetail.improvementId}
                              onClick={() => adminOperate ? setAdminExecuting(activeDetail) : navigate(repairCreatePath(activeDetail))}
                              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              {statusSavingId === activeDetail.improvementId ? "正在开始…" : adminOperate ? "管理员指定方向并进入进化室" : "进入 Bot 修复"}
                            </button>
                          )}
                          {canOperate && normalizedStatus(activeDetail.status) === "IN_PROGRESS" && !activeDetailVerifying && isAutoRepair(activeDetail) && (
                            <button
                              onClick={() => canRerunRepair(activeDetail)
                                ? setAutoRepairing(activeDetail)
                                : activeDetail.latestEvolveTaskId
                                  ? navigate(String(activeDetail.latestEvolveTaskId).startsWith("REPAIR-")
                                    ? `/evolve/repair-runs/${encodeURIComponent(activeDetail.latestEvolveTaskId)}`
                                    : `/evolve/runs/${encodeURIComponent(activeDetail.latestEvolveTaskId)}`)
                                  : setAutoRepairing(activeDetail)}
                              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition hover:bg-blue-700"
                            >
                              <InsightIcon name="external" />
                              {canRerunRepair(activeDetail) ? "重新发起修复" : "查看修复进度"}
                            </button>
                          )}
                          {canOperate && normalizedStatus(activeDetail.status) === "IN_PROGRESS" && !activeDetailVerifying && !isAutoRepair(activeDetail) && (
                            <>
                              <button
                                disabled={statusSavingId === activeDetail.improvementId}
                                onClick={() => markHandled(activeDetail)}
                                className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <InsightIcon name="check" />
                                {statusSavingId === activeDetail.improvementId ? "正在更新…" : adminOperate ? "推进到系统验收" : "我已自行修复，开始验收"}
                              </button>
                              {!adminOperate && (
                                <button
                                  type="button"
                                  onClick={() => navigate(repairCreatePath(activeDetail))}
                                  className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-white px-4 py-2.5 text-xs font-semibold text-blue-700 shadow-sm transition hover:bg-blue-50"
                                >
                                  <InsightIcon name="bot" />
                                  交给 Bot 修复
                                </button>
                              )}
                            </>
                          )}
                          {canOperate && ["ACTIVE", "IN_PROGRESS"].includes(normalizedStatus(activeDetail.status)) && (
                            <button
                              disabled={statusSavingId === activeDetail.improvementId}
                              onClick={() => setRejecting(activeDetail)}
                              className="inline-flex items-center justify-center rounded-lg bg-red-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm transition hover:bg-red-700 disabled:opacity-50"
                            >
                              驳回
                            </button>
                          )}
                          {canOperate && normalizedStatus(activeDetail.status) === "ARCHIVED" && (
                            <button
                              disabled={statusSavingId === activeDetail.improvementId}
                              onClick={() => changeStatus(activeDetail, "ACTIVE")}
                              className="rounded-lg bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white transition hover:bg-blue-700 disabled:opacity-50"
                            >
                              {statusSavingId === activeDetail.improvementId ? "恢复中…" : "恢复处理"}
                            </button>
                          )}
                        </div>
                        <p className="text-left text-xs leading-5 text-gray-500">
                          {readOnly && !adminOperate ? "仅查看对应用户的处理进度和 Agent 验收结果。" : repairActionHint(activeDetail)}
                        </p>
                      </div>
                    </div>
                  </section>
                  <div className={`grid gap-5 ${activeDetail.assignmentReason ? "lg:grid-cols-2" : ""}`}>
                    {activeDetail.assignmentReason && <section className="rounded-2xl border border-amber-200 bg-amber-50/70 p-5">
                      <div className="flex items-center gap-2 text-xs font-semibold text-amber-800">
                        <InsightIcon name="message" />
                        处理原因
                      </div>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-amber-950">
                        {activeDetail.assignmentReason}
                      </p>
                    </section>}
                    <section className="rounded-2xl border border-blue-200 bg-blue-50/70 p-5">
                      <div className="flex items-center gap-2 text-xs font-semibold text-blue-800">
                        <InsightIcon name="bot" />
                        建议处理方式
                      </div>
                      <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-blue-950">
                        {activeDetail.suggestedAction || activeDetail.userGuidance ||
                          "请结合代表失败案例完成最小范围处理。"}
                      </p>
                    </section>
                  </div>
                  <section className="rounded-2xl border border-cyan-200 bg-cyan-50/70 p-5">
                    <div className="flex items-center gap-2 text-xs font-semibold text-cyan-800">
                      <InsightIcon name="refresh" />
                      Agent 自动验收
                    </div>
                    <p className="mt-2 text-sm leading-6 text-cyan-950">
                      {verificationText(activeDetail)}
                    </p>
                    {activeDetail.verificationLastCheckedAt && (
                      <p className="mt-2 text-xs text-cyan-700">
                        最近检查：{formatDateTime(activeDetail.verificationLastCheckedAt)}
                        {activeDetail.verificationLastRecurrenceAt
                          ? ` · 最后再次出现：${formatDateTime(activeDetail.verificationLastRecurrenceAt)}`
                          : ""}
                      </p>
                    )}
                  </section>
                  {normalizedStatus(activeDetail.status) === "ARCHIVED" && (
                    <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                      <h3 className="text-sm font-semibold text-gray-900">驳回记录</h3>
                      <p className="mt-2 text-sm text-gray-700">
                        {rejectReasons.find(([code]) => code === activeDetail.rejectReasonCode)?.[1]
                          || activeDetail.rejectReasonCode
                          || "已驳回"}
                      </p>
                      {activeDetail.rejectComment && (
                        <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-gray-600">
                          {activeDetail.rejectComment}
                        </p>
                      )}
                      <p className="mt-2 text-[11px] text-gray-400">
                        Governance Agent 会读取当前 Bot 最近 15 天的驳回记录和问题上下文，再判断是否需要重新创建。
                      </p>
                    </section>
                  )}
                  <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                    <h3 className="text-sm font-semibold text-gray-900">
                      基本信息
                    </h3>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      <Info
                        label="处理用户"
                        value={activeDetail.ownerUserId}
                        mono
                      />
                      <Info
                        label="Bot Owner"
                        value={activeDetail.botOwnerUserId}
                        mono
                      />
                      <Info
                        label="发起人"
                        value={activeDetail.createdBy}
                        mono
                      />
                      <Info
                        label="问题规模"
                        value={`${activeDetail.evidenceCount} 个失败任务`}
                      />
                      <Info
                        label="涉及 Session"
                        value={`${activeDetail.sessionCount} 个`}
                      />
                      <Info
                        label="数据范围"
                        value={
                          activeDetail.dataStartTime && activeDetail.dataEndTime
                            ? `${formatDateTime(activeDetail.dataStartTime)} 至 ${formatDateTime(activeDetail.dataEndTime)}`
                            : "—"
                        }
                      />
                      <Info
                        label="创建时间"
                        value={formatDateTime(activeDetail.gmtCreate)}
                      />
                      <Info
                        label="最近更新"
                        value={formatDateTime(activeDetail.gmtModified)}
                      />
                    </div>
                  </section>
                  <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                    <div className="flex items-center justify-between">
                      <div>
                        <h3 className="text-sm font-semibold text-gray-900">
                          失败任务证据
                        </h3>
                        <p className="mt-1 text-xs text-gray-400">
                          创建时冻结的任务摘要。
                        </p>
                      </div>
                      <span className="text-xs text-gray-400">
                        {activeDetail.evidence.length} 条
                      </span>
                    </div>
                    <div className="mt-3 divide-y divide-gray-100 rounded-xl border border-gray-200">
                      {activeDetail.evidence.map((evidence) => (
                        <article
                          key={`${evidence.sessionId}:${evidence.taskIndex}`}
                          className="p-4"
                        >
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <p className="text-sm font-medium leading-5 text-gray-900">
                                {evidence.taskDescription}
                              </p>
                              <p className="mt-1 truncate font-mono text-[10px] text-gray-400">
                                {evidence.sessionId} · Task {evidence.taskIndex}
                              </p>
                            </div>
                            <span className="shrink-0 rounded-full bg-orange-50 px-2.5 py-1 text-[10px] font-medium text-orange-700">
                              {failureClassText[evidence.failureClass] ??
                                evidence.failureClass}
                            </span>
                          </div>
                          <p className="mt-2 text-xs leading-5 text-gray-600">
                            {evidence.reasoningSummary || "暂无 Judge 摘要"}
                          </p>
                          <div className="mt-3 flex justify-end">
                            <button
                              type="button"
                              onClick={() => openFailureTask(activeDetail, evidence)}
                              className="inline-flex items-center gap-1.5 text-xs font-medium text-blue-600 hover:text-blue-700"
                            >
                              <InsightIcon name="external" />
                              查看失败任务详情
                            </button>
                          </div>
                        </article>
                      ))}
                    </div>
                  </section>
                  <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-semibold text-gray-900">
                        处理记录
                      </h3>
                      <span className="text-xs text-gray-400">
                        {activeDetail.evolveLinks.length} 个诊断任务
                      </span>
                    </div>
                    {activeDetail.evolveLinks.length ? (
                      <div className="mt-3 divide-y divide-gray-100 rounded-xl border border-gray-200">
                        {activeDetail.evolveLinks.map((link) => (
                          <button
                            key={link.evolveTaskId}
                            onClick={() =>
                              navigate(
                                `/evolve/runs/${encodeURIComponent(link.evolveTaskId)}`,
                              )
                            }
                            className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left hover:bg-gray-50"
                          >
                            <div>
                              <p className="text-sm font-medium text-gray-800">
                                {link.taskName || link.evolveTaskId}
                              </p>
                              <p className="mt-1 font-mono text-[10px] text-gray-400">
                                {link.evolveTaskId}
                              </p>
                            </div>
                            <div className="text-right">
                              <p className="text-xs text-gray-600">
                                {link.taskStatus || "已关联"}
                              </p>
                              <p className="mt-1 text-[10px] text-gray-400">
                                {formatDateTime(link.gmtCreate)}
                              </p>
                            </div>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-3 rounded-xl border border-dashed border-gray-300 px-4 py-5 text-center text-xs text-gray-400">
                        尚未发起诊断任务。
                      </p>
                    )}
                  </section>
                </div>
              ) : null}
            </div>
          </aside>
        </div>
      )}
      {rejecting && (
        <RejectDialog
          item={rejecting}
          adminMode={adminOperate}
          onClose={() => setRejecting(null)}
          onSubmitted={(updated) => {
            replaceImprovement(updated);
            setRejecting(null);
            setWorkView("archived");
            setHandoffMessage("已记录驳回原因，Governance Agent 后续会结合当前 Bot 最近 15 天的上下文判断是否需要重新创建。");
          }}
        />
      )}
      {failureTask && <FailureTaskDrawer task={failureTask} onClose={() => setFailureTask(null)} />}
      {autoRepairing && <AutoRepairDialog
        item={autoRepairing}
        currentUserId={currentUserId}
        onClose={() => setAutoRepairing(null)}
        onCreated={(taskId) => navigate(`/evolve/repair-runs/${encodeURIComponent(taskId)}`)}
      />}
      {adminExecuting && <AdminExecuteDialog
        item={adminExecuting}
        onClose={() => setAdminExecuting(null)}
        onDone={(taskId) => {
          setAdminExecuting(null);
          setHandoffMessage(`管理员已创建一次性修复任务 ${taskId}，改进项已进入处理中，完成后会自动验收。`);
          setWorkView("processing");
          setReloadKey((value) => value + 1);
        }}
      />}
      {grantManagerVisible && <AutoRepairGrantManager onClose={() => setGrantManagerVisible(false)} />}
    </div>
  );
}

function AutoRepairDialog({
  item,
  currentUserId,
  onClose,
  onCreated,
}: {
  item: ImprovementDetail;
  currentUserId: string;
  onClose: () => void;
  onCreated: (taskId: string) => void;
}) {
  const [bots, setBots] = useState<TCLogBot[]>([]);
  const [botId, setBotId] = useState("");
  const [grantMode, setGrantMode] = useState<"once" | "persistent">("once");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [requestId] = useState(() => createRequestId("insight-auto-repair"));

  useEffect(() => {
    let active = true;
    if (!currentUserId) {
      queueMicrotask(() => {
        if (!active) return;
        setError("无法识别当前用户，请重新登录");
        setLoading(false);
      });
      return () => { active = false; };
    }
    api.tclog.bots({ ownerId: currentUserId, status: "all" })
      .then((result) => {
        if (!active) return;
        const available = result.bots.filter((bot) => (
          bot.botType?.toLowerCase() !== "service"
          && (!bot.activeEngine || bot.activeEngine.toLowerCase() === "openclaw")
        ));
        setBots(available);
        const sourceBot = item.botOwnerUserId === currentUserId
          ? available.find((bot) => bot.botId === item.botId)
          : undefined;
        setBotId(sourceBot?.botId ?? "");
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "测试 Bot 列表加载失败");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [currentUserId, item.botId, item.botOwnerUserId]);

  const selectedBot = bots.find((bot) => bot.botId === botId);
  const crossBot = Boolean(botId && (
    item.botOwnerUserId !== currentUserId || item.botId !== botId
  ));
  const submitLabel = grantMode === "persistent"
    ? "持续授权并创建自动修复任务"
    : "仅授权本次并创建自动修复任务";

  return <div className="fixed inset-0 z-[85] flex items-center justify-center bg-gray-950/40 p-4 backdrop-blur-sm" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div role="dialog" aria-modal="true" aria-label="自动修复" className="flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
      <div className="flex items-start justify-between border-b border-gray-100 px-6 py-5">
        <div><p className="text-sm font-semibold text-gray-950">自动修复</p><p className="mt-1 text-xs leading-5 text-gray-500">确认测试 Bot 和授权方式后，系统会直接创建进化任务。</p></div>
        <button onClick={onClose} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><InsightIcon name="close" /></button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
        <section className="rounded-xl border border-blue-200 bg-blue-50/70 p-4">
          <div className="flex items-start gap-3"><span className="mt-0.5 text-blue-700"><InsightIcon name="bot" /></span><div><p className="text-sm font-semibold text-blue-950">只在测试 Bot 上模拟和修改</p><p className="mt-1 text-xs leading-5 text-blue-800">系统会在你选择的测试 Bot 上模拟失败请求和回归请求，并可能修改该测试 Bot 的 Skill、工具约束或其他配置项。服务 Bot 不会出现在列表中，也不会被修改。</p></div></div>
        </section>

        <section>
          <p className="text-xs font-semibold text-gray-700">测试 Bot</p>
          <select aria-label="测试 Bot" value={botId} disabled={loading} onChange={(event) => setBotId(event.target.value)} className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500">
            <option value="">{loading ? "正在加载测试 Bot…" : "请选择测试 Bot"}</option>
            {bots.map((bot) => <option key={bot.botId} value={bot.botId}>{bot.botName || bot.displayBotId} · {bot.botId}</option>)}
          </select>
          {!loading && bots.length === 0 && !error && <p className="mt-2 text-xs text-amber-700">当前没有可用于自动修复的非服务型 OpenClaw 测试 Bot。</p>}
          {selectedBot && <p className="mt-2 text-xs text-gray-500">执行目标：<span className="font-mono">{currentUserId} / {selectedBot.botId}</span>{crossBot ? ` · 失败证据来自 ${item.botOwnerUserId} / ${item.botId}` : ""}</p>}
        </section>

        <section>
          <p className="text-xs font-semibold text-gray-700">授权方式</p>
          <p className="mt-1 text-xs leading-5 text-gray-500">必须由你手动选择；默认只授权本次。</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className={`cursor-pointer rounded-xl border p-4 transition ${grantMode === "once" ? "border-blue-400 bg-blue-50 ring-2 ring-blue-500/10" : "border-gray-200 hover:bg-gray-50"}`}>
              <div className="flex items-start gap-3"><input type="radio" name="insight-auto-repair-mode" checked={grantMode === "once"} onChange={() => setGrantMode("once")} className="mt-0.5 h-4 w-4" /><span><span className="block text-sm font-semibold text-gray-900">仅本次授权</span><span className="mt-1 block text-xs leading-5 text-gray-500">只处理当前改进项；以后出现同类问题时需要重新确认。</span></span></div>
            </label>
            <label className={`cursor-pointer rounded-xl border p-4 transition ${grantMode === "persistent" ? "border-emerald-400 bg-emerald-50 ring-2 ring-emerald-500/10" : "border-gray-200 hover:bg-gray-50"}`}>
              <div className="flex items-start gap-3"><input type="radio" name="insight-auto-repair-mode" checked={grantMode === "persistent"} onChange={() => setGrantMode("persistent")} className="mt-0.5 h-4 w-4" /><span><span className="block text-sm font-semibold text-gray-900">持续授权同类问题</span><span className="mt-1 block text-xs leading-5 text-gray-500">仅在同一用户、测试 Bot 和治理规则未变化时复用；可随时撤销。</span></span></div>
            </label>
          </div>
          <p className={`mt-3 rounded-lg px-3 py-2.5 text-xs leading-5 ${grantMode === "persistent" ? "bg-emerald-50 text-emerald-800" : "bg-gray-50 text-gray-600"}`}>{grantMode === "persistent" ? "持续授权会被保存。后续同类改进项仍需 Admin 批准；批准后可直接进入自动修复和验收，无需再次向你确认。" : "本次任务会记录为仅本次授权，不会留下可供后续自动执行的 Owner 授权。"}</p>
        </section>

        {error && <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">{error}</p>}
      </div>
      <div className="flex justify-end gap-2 border-t border-gray-100 px-6 py-4">
        <button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2.5 text-xs font-medium text-gray-600 hover:bg-gray-50">取消</button>
        <button disabled={submitting || loading || !selectedBot} onClick={async () => {
          if (!selectedBot) return;
          setSubmitting(true); setError("");
          try {
            const task = await api.repair.create({
              agentMode: "openclaw",
              llmUseDefault: true,
              taskName: `${item.title.slice(0, 119)} · 自动修复`,
              symptom: item.title,
              repairDirection: (item.suggestedAction ?? item.userGuidance ?? "来自 Insight Center 的自动修复项").slice(0, 5000),
              botId: selectedBot.botId,
              crossBotConfirmed: crossBot,
              persistAutoRepairGrant: grantMode === "persistent",
              insightImprovementId: item.improvementId,
              insightRequestId: requestId,
            });
            onCreated(task.taskId);
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : "自动修复任务创建失败");
          } finally {
            setSubmitting(false);
          }
        }} className="rounded-lg bg-blue-600 px-4 py-2.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{submitting ? "正在创建…" : submitLabel}</button>
      </div>
    </div>
  </div>;
}

function AutoRepairGrantManager({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<AutoRepairGrantView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revokingId, setRevokingId] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    insightApi.autoRepairGrants()
      .then((result) => { if (active) setItems(result.items); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "授权列表加载失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  return <div className="fixed inset-0 z-[85] flex items-center justify-center bg-gray-950/40 p-4 backdrop-blur-sm" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div role="dialog" aria-modal="true" className="flex max-h-[82vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
      <div className="flex items-start justify-between border-b border-gray-100 px-6 py-5">
        <div><p className="text-sm font-semibold text-gray-950">自动修复授权</p><p className="mt-1 text-xs leading-5 text-gray-500">授权只在同一用户、Bot、环境、规则版本、允许修改目标和风险等级完全一致时生效；撤销后新问题不会再自动执行。</p></div>
        <button onClick={onClose} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><InsightIcon name="close" /></button>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        {error && <p className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">{error}</p>}
        {loading ? <LoadingPanel text="正在读取授权…" /> : items.length === 0 ? <EmptyPanel title="暂无自动修复授权" description="首次选择“持续授权同类问题”后，会在这里生成可撤销的授权记录。" /> : <div className="space-y-3">{items.map((grant) => <article key={grant.grantId} className={`rounded-xl border p-4 ${grant.status === "ACTIVE" ? "border-emerald-200 bg-emerald-50/50" : "border-gray-200 bg-gray-50"}`}>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className={`rounded-full px-2.5 py-1 text-[10px] font-semibold ${grant.status === "ACTIVE" ? "bg-emerald-100 text-emerald-700" : "bg-gray-200 text-gray-600"}`}>{grant.status === "ACTIVE" ? "已生效" : "已撤销"}</span><span className="text-xs font-semibold text-gray-900">{grant.botId}</span><span className="text-[10px] text-gray-400">{grant.environment}</span></div><p className="mt-2 font-mono text-xs text-gray-700">{grant.sourceRuleId} · v{grant.ruleVersion}</p><p className="mt-2 text-xs leading-5 text-gray-600">允许修改：{grant.allowedTargets.length ? grant.allowedTargets.join("、") : "无"} · 风险：{grant.risk}</p><p className="mt-1 text-[10px] text-gray-400">授权时间 {formatDateTime(grant.grantedAt)} · 来源改进项 #{grant.sourceImprovementId}</p></div>
            {grant.status === "ACTIVE" && <button disabled={revokingId === grant.grantId} onClick={async () => {
              if (!window.confirm("撤销后，后续同类问题不会再自动修复。确认撤销？")) return;
              setRevokingId(grant.grantId); setError("");
              try {
                const updated = await insightApi.revokeAutoRepairGrant(grant.grantId, grant.version);
                setItems((current) => current.map((item) => item.grantId === updated.grantId ? updated : item));
              } catch (reason) {
                setError(reason instanceof Error ? reason.message : "撤销授权失败");
              } finally {
                setRevokingId(null);
              }
            }} className="rounded-lg border border-red-200 bg-white px-3 py-2 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50">{revokingId === grant.grantId ? "撤销中…" : "撤销授权"}</button>}
          </div>
        </article>)}</div>}
      </div>
      <div className="flex justify-end border-t border-gray-100 px-6 py-4"><button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2.5 text-xs font-medium text-gray-600 hover:bg-gray-50">关闭</button></div>
    </div>
  </div>;
}

function RejectDialog({
  item,
  adminMode = false,
  onClose,
  onSubmitted,
}: {
  item: ImprovementView;
  adminMode?: boolean;
  onClose: () => void;
  onSubmitted: (updated: ImprovementView) => void;
}) {
  const [reasonCode, setReasonCode] = useState<(typeof rejectReasons)[number][0]>(
    "EXPECTED_BUSINESS_FAILURE",
  );
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const invalid = reasonCode === "OTHER" && !comment.trim();

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-gray-950/40 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" className="w-full max-w-xl overflow-hidden rounded-2xl bg-white shadow-2xl">
        <div className="flex items-start justify-between border-b border-gray-100 px-6 py-5">
          <div>
            <p className="text-sm font-semibold text-gray-950">驳回这个改进项</p>
            <p className="mt-1 text-xs leading-5 text-gray-500">
              填写原因后，Governance Agent 会把它纳入后续判断，减少同类问题再次打扰。
            </p>
          </div>
          <button onClick={onClose} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100">
            <InsightIcon name="close" />
          </button>
        </div>
        <div className="space-y-5 px-6 py-5">
          <div>
            <p className="mb-2 text-xs font-semibold text-gray-700">驳回原因</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {rejectReasons.map(([code, label]) => (
                <label
                  key={code}
                  className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2.5 text-xs ${reasonCode === code ? "border-blue-300 bg-blue-50 text-blue-800" : "border-gray-200 text-gray-600 hover:bg-gray-50"}`}
                >
                  <input
                    type="radio"
                    checked={reasonCode === code}
                    onChange={() => setReasonCode(code)}
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>
          <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-3">
            <p className="text-xs font-semibold text-violet-800">抑制范围</p>
            <p className="mt-1 text-xs leading-5 text-violet-700">
              当前处理用户 + 当前 Bot 的最近 15 天上下文会提供给 Governance Agent，由 Agent 判断是否仍需创建 Action。
            </p>
          </div>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-gray-600">
              补充说明{reasonCode === "OTHER" ? "（必填）" : "（可选）"}
            </span>
            <textarea
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              maxLength={2000}
              rows={4}
              className="w-full rounded-xl border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-500"
              placeholder="补充业务背景或判断依据…"
            />
          </label>
          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-gray-100 px-6 py-4">
          <button onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2.5 text-xs font-medium text-gray-600 hover:bg-gray-50">
            取消
          </button>
          <button
            disabled={submitting || invalid}
            onClick={async () => {
              setSubmitting(true);
              setError("");
              try {
                onSubmitted(await (adminMode ? insightApi.adminRejectImprovement : insightApi.rejectImprovement)(item.improvementId, {
                  reasonCode,
                  comment: comment.trim() || undefined,
                  version: item.version,
                }));
              } catch (reason) {
                setError(reason instanceof Error ? reason.message : "驳回失败");
              } finally {
                setSubmitting(false);
              }
            }}
            className="rounded-lg bg-red-600 px-4 py-2.5 text-xs font-semibold text-white hover:bg-red-700 disabled:opacity-50"
          >
            {submitting ? "正在提交…" : "确认驳回"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Info({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-xl bg-gray-50 p-3">
      <p className="text-[10px] text-gray-400">{label}</p>
      <p
        className={`mt-1 break-words text-xs font-medium text-gray-700 ${mono ? "font-mono" : ""}`}
      >
        {value}
      </p>
    </div>
  );
}
