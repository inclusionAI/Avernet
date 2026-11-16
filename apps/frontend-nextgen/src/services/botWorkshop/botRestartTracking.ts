import type { BotDomain, BotRestartTracking } from '@/domain/botWorkshop';
import { getBot } from '@/services/backendApi/bots/botController';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';

/**
 * Coding Bot（aicoding/claude_code）的重启走异步 journal：mutation 返回时容器尚未替换，
 * `status` 仍为 ACTIVE，真实进度在 `ext.aicoding.restart.operation_id` 上；库存读到过渡态前
 * 需要本地钉住「重启中」展示。其他引擎（如 openclaw）的 restart 同步落库 PENDING，
 * 直接采信响应里的状态即可。
 */
export const CODING_RESTART_PIN_TTL_MS = 5 * 60_000;

const SERVER_TRANSITIONAL_STATUSES = new Set(['PENDING', 'PROVISIONING', 'REACTIVATING']);

/** Restart mutation 响应中的 Bot 记录子集：状态列与 coding 重启 journal 扩展。 */
export interface BotRestartResponse {
  status?: string;
  ext?: {
    aicoding?: {
      restart?: {
        operation_id?: unknown;
        status?: unknown;
        phase?: unknown;
        message?: unknown;
      };
    };
  };
}

export interface CodingRestartRecord {
  operationId: string;
  status: string;
  phase?: string;
  message?: string;
}

export function readCodingRestartRecord(dto: unknown): CodingRestartRecord | undefined {
  const record = (dto as BotRestartResponse | undefined)?.ext?.aicoding?.restart;
  if (!record) return undefined;
  const operationId = typeof record.operation_id === 'string' ? record.operation_id.trim() : '';
  const status = typeof record.status === 'string' ? record.status.trim().toUpperCase() : '';
  if (!operationId || !status) return undefined;
  return {
    operationId,
    status,
    phase: typeof record.phase === 'string' ? record.phase : undefined,
    message: typeof record.message === 'string' ? record.message : undefined,
  };
}

function botKey(bot: Pick<BotDomain, 'id' | 'ownerId'>): string {
  return `${bot.ownerId}:${bot.id}`;
}

function clearPendingRestart(item: BotDomain): BotDomain {
  if (!item.pendingRestart) return item;
  const { pendingRestart, ...rest } = item;
  void pendingRestart;
  return rest as BotDomain;
}

/** 重启成功后立即用响应修正列表内该 Bot 的展示：coding 建 pin，其他引擎直接用响应状态。 */
function withRecordedStatus(item: BotDomain, status: string): BotDomain {
  return {
    ...item,
    rawStatus: status,
    rawDisplayState: undefined,
    lifecycle: status.toUpperCase() === 'ACTIVE' ? 'running' : 'deploying',
  };
}

export function withRestartResponse(
  item: BotDomain,
  response: BotRestartResponse | undefined,
  now: number = Date.now(),
): BotDomain {
  const codingRestart = readCodingRestartRecord(response);
  if (codingRestart?.status === 'RUNNING') {
    return {
      ...item,
      rawStatus: 'PENDING',
      rawDisplayState: undefined,
      lifecycle: 'deploying',
      pendingRestart: {
        operationId: codingRestart.operationId,
        startedAt: now,
        deadlineAt: now + CODING_RESTART_PIN_TTL_MS,
        extensions: 0,
      },
    };
  }
  if (codingRestart?.status === 'FAILED') {
    return {
      ...item,
      rawStatus: 'FAILED',
      rawDisplayState: undefined,
      lifecycle: 'failed',
    };
  }
  const status = typeof response?.status === 'string' ? response.status.trim() : '';
  if (!status) return item;
  return withRecordedStatus(item, status);
}

export function applyRestartOutcome(
  bot: BotDomain,
  response: BotRestartResponse | undefined,
  now: number = Date.now(),
): void {
  useBotWorkshopStore.setState((state) => ({
    items: state.items.map((item) =>
      item.id === bot.id && item.ownerId === bot.ownerId ? withRestartResponse(item, response, now) : item,
    ),
  }));
}

/**
 * 库存同步合并规则：未读到过渡态前的 ACTIVE 是 coding journal QUEUED/BACKING_UP 阶段的
 * 滞后值，不允许打断「重启中」展示；读到过渡态后转为跟随服务端状态机直至完成。
 */
export function reconcileRestartPins(previous: BotDomain[], fresh: BotDomain[]): BotDomain[] {
  const pins = new Map<string, BotRestartTracking>();
  for (const item of previous) {
    if (item.pendingRestart) pins.set(botKey(item), item.pendingRestart);
  }
  if (!pins.size) return fresh;
  return fresh.map((item) => {
    const pin = pins.get(botKey(item));
    if (!pin) return item;
    const status = (item.rawStatus || '').toUpperCase();
    if (SERVER_TRANSITIONAL_STATUSES.has(status)) {
      return { ...item, pendingRestart: { ...pin, sawProgress: true } };
    }
    if (status === 'ACTIVE') {
      if (pin.sawProgress) return clearPendingRestart(item);
      return {
        ...item,
        rawStatus: 'PENDING',
        rawDisplayState: undefined,
        lifecycle: 'deploying',
        pendingRestart: pin,
      };
    }
    // FAILED/离线/回收等服务端终态比本地 pin 更可信，立即回落列表真值。
    return clearPendingRestart(item);
  });
}

export function needsFastInventorySync(bot: BotDomain): boolean {
  return Boolean(bot.pendingRestart) || bot.rawStatus?.toUpperCase() === 'REACTIVATING';
}

export type ExpiredRestartResolution =
  | { status: 'SUCCEEDED' }
  | { status: 'FAILED'; message?: string }
  | { status: 'RUNNING'; extended: boolean }
  | { status: 'UNKNOWN' };

function setRestartPin(bot: BotDomain, pin: BotRestartTracking | undefined): void {
  useBotWorkshopStore.setState((state) => ({
    items: state.items.map((item) =>
      item.id === bot.id && item.ownerId === bot.ownerId
        ? pin
          ? { ...item, pendingRestart: pin }
          : clearPendingRestart(item)
        : item,
    ),
  }));
}

/**
 * Pin 超时后的详情接口兜底：用重启响应中记录的 operation_id 对比 journal 终态。
 * 慢备份等真实长操作只延长一次观察窗口；其余情况回落列表真值，避免本地展示卡死。
 */
export async function resolveExpiredRestartPin(bot: BotDomain): Promise<ExpiredRestartResolution> {
  const pin = bot.pendingRestart;
  if (!pin) return { status: 'UNKNOWN' };
  let restart: CodingRestartRecord | undefined;
  try {
    const response = await getBot(bot.id, bot.ownerId);
    restart = readCodingRestartRecord(response.data);
  } catch {
    setRestartPin(bot, undefined);
    return { status: 'UNKNOWN' };
  }
  if (restart?.operationId !== pin.operationId) {
    setRestartPin(bot, undefined);
    return { status: 'UNKNOWN' };
  }
  if (restart.status === 'SUCCEEDED') {
    setRestartPin(bot, undefined);
    return { status: 'SUCCEEDED' };
  }
  if (restart.status === 'FAILED') {
    setRestartPin(bot, undefined);
    return { status: 'FAILED', message: restart.message };
  }
  if (restart.status === 'RUNNING' && (pin.extensions ?? 0) < 1) {
    setRestartPin(bot, {
      ...pin,
      deadlineAt: Date.now() + CODING_RESTART_PIN_TTL_MS,
      extensions: (pin.extensions ?? 0) + 1,
    });
    return { status: 'RUNNING', extended: true };
  }
  setRestartPin(bot, undefined);
  return { status: 'RUNNING', extended: false };
}
