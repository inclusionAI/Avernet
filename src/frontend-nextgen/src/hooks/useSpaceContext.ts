// 全局空间上下文 Hook：统一读写入口。
// - useSpaceContext(selector): 选读 store（各模块用：const spaceId = useSpaceContext(s=>s.currentSpaceId)）
// - ensurePersonalSpaceOnAppEntry(): App 挂载（进入项目）时由 AppShell 调用，单飞初始化一次个人空间（幂等，失败静默）
// - initSpaceContext(): 进入管理区域时调用，拉已加入空间（scope=accessible）→首次无个人空间则 ensure+重拉→还原/默认
//   （localStorage tc_personal_space_ensured 幂等标记，避免每次进管理页重复「查+建」往返）
// - refreshSpaceContext(): 切换器气泡每次打开时调用，重拉最新列表（保留当前选中；失效则回落个人空间）
// - switchSpaceContext(id): 切换器选择时调用，更新 store + 写 localStorage
// 详细接入说明见 src/pages/Admin/README.md
// 设计依据：docs/specs/2026-08-17-global-space-context-switcher/plan.md §2/§3。
// Hook ≤ 150 行。localStorage key 用统一前缀风格（对齐 ocb storage.ts 范式）。
import type { Space } from '@/domain/admin/models';
import { pickDefaultSpace } from '@/domain/spaceContext';
import { adminService } from '@/services/admin';
import { useSpaceContextStore } from '@/stores/spaceContextStore';

const STORAGE_KEY = 'tc_space_context_current_id';
// 个人空间幂等标记：进管理页时若为 '1' 跳过 ensure（避免每次查+建往返）。
// 失效场景极罕见（手动清后端数据），下次 listSpaces 无 PERSONAL 时仍会兜底重建并重设标记。
const ENSURED_KEY = 'tc_personal_space_ensured';

function readStoredId(): number | undefined {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return undefined;
    const n = Number(raw);
    return Number.isFinite(n) && n > 0 ? n : undefined;
  } catch {
    return undefined;
  }
}

function writeStoredId(id: number | undefined): void {
  try {
    if (id === undefined) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    /* localStorage 不可用时静默降级，不阻断切换 */
  }
}

/** 是否已确保个人空间（localStorage 幂等标记）。localStorage 不可用视为未确保（保守重建）。 */
function isPersonalEnsured(): boolean {
  try {
    return localStorage.getItem(ENSURED_KEY) === '1';
  } catch {
    return false;
  }
}

function markPersonalEnsured(): void {
  try {
    localStorage.setItem(ENSURED_KEY, '1');
  } catch {
    /* 只读 localStorage 时静默降级，不影响主流程 */
  }
}

/** 在已加入子集中校验 id 是否存在。 */
function idInSpaces(spaces: Space[], id: number | undefined): boolean {
  return id === undefined ? false : spaces.some((s) => s.spaceId === id);
}

/** 拉已加入空间子集（后端 scope=accessible 已过滤，前端不再二次过滤）；失败返回 error 文案。 */
async function fetchJoinedSpaces(): Promise<{ spaces?: Space[]; error?: string }> {
  const r = await adminService.listSpaces({ page: 1, pageSize: 100, scope: 'accessible' });
  if (r.error) return { error: r.error.message || '加载空间列表失败' };
  return { spaces: r.data?.items ?? [] };
}

export function useSpaceContext<T>(selector: (s: ReturnType<typeof useSpaceContextStore.getState>) => T): T {
  return useSpaceContextStore(selector);
}

/** 进入管理区域时初始化：拉可见全集→过滤已加入→还原/默认。幂等（成功后跳过；失败可重试）。 */
export async function initSpaceContext(): Promise<void> {
  const store = useSpaceContextStore.getState();
  if (store.initialized) return;
  store.setLoading(true);
  store.setError(undefined);
  const r = await fetchJoinedSpaces();
  if (r.error) {
    store.setLoading(false);
    store.setError(r.error);
    return;
  }
  let joined = r.spaces ?? [];
  // 首次无个人空间且未 ensure → 初始化个人空间并重拉一次（幂等：localStorage 标记避免每次往返）
  if (!isPersonalEnsured() && !joined.some((s) => s.spaceType === 'PERSONAL')) {
    const ensureR = await adminService.ensurePersonalSpace();
    if (!ensureR.error) {
      markPersonalEnsured();
      // 重拉已加入列表，纳入新建个人空间
      const r2 = await fetchJoinedSpaces();
      if (!r2.error) joined = r2.spaces ?? [];
    }
    // ensure/重拉失败静默降级，沿用首次列表兜底（不阻断初始化）
  } else if (joined.some((s) => s.spaceType === 'PERSONAL')) {
    // 列表已含个人空间，同步标记（覆盖手动清后端数据后重建的场景）
    markPersonalEnsured();
  }
  store.setSpaces(joined);
  const stored = readStoredId();
  const initialId = idInSpaces(joined, stored) ? stored : pickDefaultSpace(joined)?.spaceId;
  store.setCurrentSpaceId(initialId);
  if (initialId !== stored) writeStoredId(initialId);
  store.setInitialized(true);
  store.setLoading(false);
}

/** 气泡每次打开时刷新：重拉已加入子集。当前空间失效（被移出/删除）时回落个人空间并修正持久化。 */
export async function refreshSpaceContext(): Promise<void> {
  const store = useSpaceContextStore.getState();
  if (store.loading) return; // 与 init / 上一次 refresh 去重，避免并发重拉
  store.setLoading(true);
  store.setError(undefined);
  const r = await fetchJoinedSpaces();
  if (r.error) {
    // 刷新失败保留旧列表，仅置 error（不打断已有数据的展示）
    store.setLoading(false);
    store.setError(r.error);
    return;
  }
  const joined = r.spaces ?? [];
  store.setSpaces(joined); // setSpaces 会把已不在列表中的 currentSpaceId 置 undefined
  const after = useSpaceContextStore.getState();
  if (after.currentSpaceId === undefined) {
    const fallback = pickDefaultSpace(joined)?.spaceId;
    after.setCurrentSpaceId(fallback);
    writeStoredId(fallback);
  }
  after.setLoading(false);
}

/** 切换当前空间：更新 store + 持久化 localStorage。 */
export function switchSpaceContext(id: number): void {
  useSpaceContextStore.getState().setCurrentSpaceId(id);
  writeStoredId(id);
}

export interface UseSpaceContextActions {
  init: typeof initSpaceContext;
  refresh: typeof refreshSpaceContext;
  switchSpace: typeof switchSpaceContext;
}

/** 组件用：返回稳定 actions 引用（模块级常量，引用永恒定）。 */
export function useSpaceContextActions(): UseSpaceContextActions {
  return { init: initSpaceContext, refresh: refreshSpaceContext, switchSpace: switchSpaceContext };
}

// App 挂载即确保个人空间的单飞标记：同一页面加载只发一次 initialize。
let appEntryEnsurePromise: Promise<void> | null = null;

/**
 * 进入项目（App 挂载）即初始化一次个人空间：不再等进入管理区域才由 initSpaceContext 兜底。
 * - 幂等：initialize 后端已存在则返回已有，每次进入重复调用安全；
 * - 单飞：同一页面加载内多次触发（含 AppShell 重挂载）只发一次请求；失败同样保留标记，本页不重试；
 * - 失败静默降级：不重抛、不阻断启动，后续进入管理区域时 initSpaceContext 的 ensure 分支按列表兜底；
 *   身份解析复用 ensureUserId → identityService 单飞，与 AppShell 挂载的身份拉取共用 inflight，零重复请求。
 */
export function ensurePersonalSpaceOnAppEntry(): Promise<void> {
  if (appEntryEnsurePromise) return appEntryEnsurePromise;
  appEntryEnsurePromise = adminService.ensurePersonalSpace().then(
    () => undefined,
    () => undefined, // 兜底吞运行时异常，避免外泄到启动链路；错误提示默认走 errorNotify 观察者
  );
  return appEntryEnsurePromise;
}
