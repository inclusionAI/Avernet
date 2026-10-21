/**
 * 副屏(聊天右侧面板)拖拽宽度记忆。
 *
 * 背景(SDK 宽度解析序:activeTabTemporaryWidth ?? tab.defaultWidth ?? 容器级拖宽 ?? 面板默认):
 * V2 任务副屏按 spec §3.1-2 给每个 task tab 声明 defaultWidth(对齐 PRD demo 默认宽 420)。
 * SDK 语义下 defaultWidth 优先于容器级拖宽,且带 defaultWidth 的 tab 拖拽只作为
 * 「当前 tab 本次停留」的临时覆盖——切换任务/刷新重开 tab 即回落 420,用户拖过的
 * 宽度丢失(对比 V1 容器级宽度全局复用,体感"变窄")。
 *
 * 补偿:ChatLayoutPanelSlot 观察 Root cloneElement 注入的拖拽结果
 * (dynamicWidth 百分比 + containerWidth),换算 px 持久化到 localStorage;
 * 两个任务 tab 开口(声明式消息 taskPanelMessage、切换条 TaskPanelSwitcherSlot)
 * openTab 时以记忆宽为 defaultWidth,无记忆回落初始 420——对齐 PRD
 * 「默认 420px,可拖拽 320–720px」且切任务不回落。
 */

/** PRD demo 副屏默认宽(spec §3.1-2);同时也是记忆缺失时的回落值。 */
export const TASK_PANEL_INITIAL_WIDTH = 420;
const PANEL_WIDTH_MIN_PX = 320;
const PANEL_WIDTH_MAX_PX = 720;
export const PANEL_WIDTH_STORAGE_KEY = 'teamclaw:workspace-right-panel-width';

/** 钳制到 PRD 拖拽范围 320–720(SDK 窄容器下拖拽百分比可换算出界外 px)。 */
const clampRange = (px: number): number =>
  Math.round(Math.min(PANEL_WIDTH_MAX_PX, Math.max(PANEL_WIDTH_MIN_PX, px)));

/** 读取持久化拖宽(px);无记忆/垃圾值一律 null(按无记忆处理,不抛异常)。 */
export function readPersistedPanelWidthPx(): number | null {
  if (typeof window === 'undefined') return null;
  const raw = window.localStorage.getItem(PANEL_WIDTH_STORAGE_KEY);
  if (!raw) return null;
  const px = Number(raw);
  if (!Number.isFinite(px)) return null;
  return clampRange(px);
}

/** Root 注入的拖拽百分比("'45.2%'")按容器宽换算 px 落盘;非法输入忽略、不动既有记忆。 */
export function persistPanelWidthPercent(percent: unknown, containerWidth: unknown): void {
  if (typeof window === 'undefined' || typeof percent !== 'string') return;
  const parsed = Number.parseFloat(percent);
  if (!Number.isFinite(parsed) || parsed <= 0) return;
  const container = Number(containerWidth);
  if (!Number.isFinite(container) || container <= 0) return;
  window.localStorage.setItem(PANEL_WIDTH_STORAGE_KEY, String(clampRange((parsed / 100) * container)));
}

/**
 * 任务 tab openTab 时的 defaultWidth。
 *
 * 存储 key 也被文件/详情等普通右侧面板复用，它们允许收窄到 320px；不能把这类
 * 较窄记忆直接套到 TaskPanel，否则会覆盖 PRD 的 420px 默认宽。已主动扩宽的记忆
 * 仍继续恢复，避免切任务后丢失用户的扩宽结果。
 */
export function resolveTaskPanelDefaultWidth(): number {
  return Math.max(readPersistedPanelWidthPx() ?? TASK_PANEL_INITIAL_WIDTH, TASK_PANEL_INITIAL_WIDTH);
}
