/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * 桌面 Bot 启动进度推断（纯函数，容错）
 *
 * 数据来自后端透传的 start-progress data，实测契约（见「镜像下载常用状态」枚举文档）：
 *   - status           镜像下载阶段状态（主字段）：idle | pending | downloading | verifying
 *                       | completed | ready | failed | cancelled。本接口只覆盖镜像下载
 *                       （phase 0/1），ready/completed 是成功终点，之后由 bot status 轮询接管。
 *   - progress         下载百分比字符串（新契约）："23"；旧契约里曾是生命周期文案，做兼容
 *   - message          可读文案，如 "claude_code downloading"
 *   - total_bytes / downloaded_bytes / bytes_per_sec  下载指标，用于进度条与速率展示
 *   - engine_name / container_id                       透传元信息
 *   - current_phase / overall_status / error_message   旧契约字段，保留兼容
 *
 * status 走精确枚举映射（STATUS_STEP_MAP），未知值/旧 current_phase 按关键词容错映射，
 * 成败以 status/progress/overall_status 关键词判定，下载百分比只用于进度条。
 */

export interface DesktopBotStartProgressData {
  status?: string;
  progress?: string;
  message?: string;
  current_phase?: string;
  overall_status?: string;
  error_message?: string | null;
  total_bytes?: number;
  downloaded_bytes?: number;
  bytes_per_sec?: number;
}

export type DesktopStartOutcome = 'running' | 'success' | 'failed';

export interface DesktopStartProgressState {
  /** 当前步骤：0=检查 1=下载 2=启动 3=激活 */
  stepIndex: number;
  outcome: DesktopStartOutcome;
  message?: string;
  /** 下载百分比 0-100，无法解析时为 undefined */
  percent?: number;
  /** 下载明细文案，如「110.4 MB / 470.5 MB · 2.5 MB/s」，无字节信息时为 undefined */
  detail?: string;
}

/** 四个步骤的展示文案 */
export const DESKTOP_START_STEP_LABELS = ['检查运行环境', '下载运行环境', '启动 Bot', '激活就绪'] as const;

const STEP_CHECK = 0;
const STEP_DOWNLOAD = 1;
const STEP_START = 2;
const STEP_ACTIVATE = 3;

const FAIL_KEYWORDS = ['FAIL', 'ERROR', 'EXCEPTION', 'TIMEOUT', 'CANCEL'];
const SUCCESS_KEYWORDS = ['COMPLETE', 'SUCCESS', 'DONE', 'FINISH', 'READY'];

/**
 * 后端 status 权威枚举 → UI 步骤/结果 的精确映射。
 * 依据「镜像下载常用状态」文档：所有 status 都在镜像下载阶段（phase 0/1），
 * ready/completed 是本接口的成功终点（之后交由 bot status 轮询接管启动/激活）。
 */
const STATUS_STEP_MAP: Record<string, { stepIndex: number; outcome: DesktopStartOutcome }> = {
  IDLE: { stepIndex: STEP_CHECK, outcome: 'running' }, // 无下载 / 无该 bot
  PENDING: { stepIndex: STEP_DOWNLOAD, outcome: 'running' }, // 已触发下载，推流未开始
  DOWNLOADING: { stepIndex: STEP_DOWNLOAD, outcome: 'running' }, // 下载中
  VERIFYING: { stepIndex: STEP_DOWNLOAD, outcome: 'running' }, // 下载完成，校验 sha256
  COMPLETED: { stepIndex: STEP_ACTIVATE, outcome: 'success' }, // 下载+校验完成
  READY: { stepIndex: STEP_ACTIVATE, outcome: 'success' }, // 下载完成
  FAILED: { stepIndex: STEP_DOWNLOAD, outcome: 'failed' }, // 网络异常 / 磁盘不足
  CANCELLED: { stepIndex: STEP_DOWNLOAD, outcome: 'failed' }, // app 崩溃或退出
};

function includesAny(source: string, keywords: string[]): boolean {
  return keywords.some((k) => source.includes(k));
}

/**
 * 由 phase/status 关键词推断步骤；无法识别时回退到 prev（不回退、不报错）
 */
function deriveStepFromPhase(phase: string, prevStepIndex: number): number {
  if (includesAny(phase, ['READY', 'ACTIV', 'COMPLETE', 'FINISH', 'DONE'])) {
    return STEP_ACTIVATE;
  }
  if (includesAny(phase, ['START', 'LAUNCH', 'BOOT', 'PROCESS', 'RUNNING'])) {
    return STEP_START;
  }
  if (includesAny(phase, ['PULL', 'DOWNLOAD', 'IMAGE', 'FETCH'])) {
    return STEP_DOWNLOAD;
  }
  if (includesAny(phase, ['CHECK', 'ENVIRON', 'PREPAR', 'INIT', 'PEND', 'QUEUE', 'WAIT'])) {
    return STEP_CHECK;
  }
  return prevStepIndex;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

/**
 * 解析下载百分比：优先取 progress 数字，其次由 downloaded/total 字节推算。
 * progress 为生命周期文案（如 'in_progress'）时 Number(...) 为 NaN，自动跳过。
 */
function parsePercent(data: DesktopBotStartProgressData): number | undefined {
  const raw = data?.progress;
  if (raw !== undefined && raw !== null && String(raw).trim() !== '') {
    const n = Number(raw);
    if (Number.isFinite(n) && n >= 0 && n <= 100) return n;
  }
  const total = Number(data?.total_bytes);
  const downloaded = Number(data?.downloaded_bytes);
  if (total > 0 && Number.isFinite(downloaded) && downloaded >= 0) {
    return Math.min(100, Math.round((downloaded / total) * 100));
  }
  return undefined;
}

/**
 * 拼接下载明细文案：「已下载 / 总量 · 速率」，缺字节信息时返回 undefined。
 */
function buildDetail(data: DesktopBotStartProgressData): string | undefined {
  const total = Number(data?.total_bytes);
  const downloaded = Number(data?.downloaded_bytes);
  const speed = Number(data?.bytes_per_sec);
  if (!(total > 0) || !Number.isFinite(downloaded)) return undefined;
  let detail = `${formatBytes(downloaded)} / ${formatBytes(total)}`;
  if (Number.isFinite(speed) && speed > 0) {
    detail += ` · ${formatBytes(speed)}/s`;
  }
  return detail;
}

/**
 * 由 start-progress 的 data 推断出 UI 状态。
 *
 * @param data 后端返回的 data 字段
 * @param prevStepIndex 上一次推断的步骤，未知 phase 时用于维持不回退
 */
export function deriveDesktopStartProgress(
  data?: DesktopBotStartProgressData,
  prevStepIndex = STEP_CHECK,
): DesktopStartProgressState {
  const status = String(data?.status ?? '')
    .trim()
    .toUpperCase();
  const progress = String(data?.progress ?? '').toUpperCase();
  const overall = String(data?.overall_status ?? '').toUpperCase();
  const phase = String(data?.current_phase ?? '').toUpperCase();
  const errorMessage = data?.error_message || undefined;
  const message = data?.message || undefined;

  // 权威 status 精确映射（有则优先），未命中时用关键词兜底
  const mapped = STATUS_STEP_MAP[status];
  // 生命周期成败关键词来源：status（新）+ overall_status + progress（旧契约里为文案）
  const lifecycle = `${status} ${overall} ${progress}`;
  // 步骤来源：current_phase（旧）+ status（新，如 'downloading'）
  const phaseSource = `${phase} ${status}`;

  // 失败优先：精确 status 判失败、命中失败关键词，或存在非空 error_message
  if (mapped?.outcome === 'failed' || includesAny(lifecycle, FAIL_KEYWORDS) || !!errorMessage) {
    return {
      // 失败时定位到当前所处步骤并标红，不可识别则维持上一步
      stepIndex: mapped?.outcome === 'failed' ? mapped.stepIndex : deriveStepFromPhase(phaseSource, prevStepIndex),
      outcome: 'failed',
      message: errorMessage || message || 'Bot 启动失败',
    };
  }

  // 成功：精确 status 判成功、命中成功关键词，或 phase/status 已 ready/active
  if (
    mapped?.outcome === 'success' ||
    includesAny(lifecycle, SUCCESS_KEYWORDS) ||
    includesAny(phaseSource, ['READY', 'ACTIV'])
  ) {
    return { stepIndex: STEP_ACTIVATE, outcome: 'success' };
  }

  // 进行中：按精确 status / phase 定位步骤，携带下载百分比与明细
  return {
    stepIndex: mapped ? mapped.stepIndex : deriveStepFromPhase(phaseSource, prevStepIndex),
    outcome: 'running',
    message,
    percent: data ? parsePercent(data) : undefined,
    detail: data ? buildDetail(data) : undefined,
  };
}
