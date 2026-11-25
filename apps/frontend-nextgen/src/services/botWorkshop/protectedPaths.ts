import type { BotEditorResource } from '@/domain/botEditor';

/**
 * 复制 Bot「排除文件」树的受保护路径:引擎必需的目录 / 文件,不允许勾选为排除。
 * 名单与规则形态对齐老前端 `PublishExcludeModal`(origin/frontend-master,
 * src/frontend/src/pages/ServiceBot/components/PublishExcludeModal.tsx),保证
 * 「发布文件排除」与「复制排除」对系统级资源采用同一套禁排口径。
 *
 * - 共享系统目录(两引擎均必需,整子树禁排):config / skills / skills-pool
 * - 按引擎的系统 md 文件(仅引擎根层禁排):见 ENGINE_PROTECTED_MD_NAMES
 */

/** 两引擎共享的系统目录名;命中即整子树禁排(含子目录与文件)。 */
const PROTECTED_DIR_NAMES = new Set(['config', 'skills', 'skills-pool']);

/**
 * 按引擎区分的系统 md 名单(注册表对齐老前端 ENGINE_FORBIDDEN_MD_NAMES):
 *  - openclaw:工作区根落盘的 7 个 md
 *  - aicoding:CLAUDE.md / AGENTS.md(项目与代理运行指令)
 *  - claude_code:仅 CLAUDE.md(项目指令,会话自动加载)
 */
const ENGINE_PROTECTED_MD_NAMES: Record<string, readonly string[]> = {
  openclaw: ['AGENTS.md', 'BOOTSTRAP.md', 'HEARTBEAT.md', 'IDENTITY.md', 'SOUL.md', 'TOOLS.md', 'USER.md'],
  aicoding: ['CLAUDE.md', 'AGENTS.md'],
  claude_code: ['CLAUDE.md'],
};

/** 资源根目录名:部分实现把工作区根放在 `workspace/` 下,规则形态由此带前缀。 */
const RESOURCE_ROOT_DIR = 'workspace';

/** 未知引擎兜底(与老前端一致:按 OpenClaw 处理)。 */
const DEFAULT_ENGINE = 'openclaw';

/** 归一化路径:trim 空白、剥开头 `./` 前缀、末尾 `/` 后缀。 */
function normalizePath(rawPath: string): string {
  if (typeof rawPath !== 'string' || rawPath.length === 0) return rawPath;
  return rawPath
    .trim()
    .replace(/^(?:\.\/)+/, '')
    .replace(/\/+$/, '');
}

/**
 * 构建本 Bot 的受保护名集合 = 共享系统目录 + 当前引擎系统 md。
 * 未知 engineType 按 OpenClaw 兜底。
 */
export function buildProtectedNames(engineType?: string): Set<string> {
  const engine = engineType && ENGINE_PROTECTED_MD_NAMES[engineType] ? engineType : DEFAULT_ENGINE;
  return new Set([
    ...PROTECTED_DIR_NAMES,
    ...ENGINE_PROTECTED_MD_NAMES[engine],
  ]);
}

/**
 * 是否为受保护路径:受保护目录与其全部子项(如 `config`、`config/...`、
 * `workspace/config/...`)及受保护文件(`AGENTS.md`、`workspace/AGENTS.md`),
 * 兼容无资源根前缀的形态(`config/...` / `AGENTS.md`)与带 `workspace/` 前缀的形态。
 *
 * 判定取首段(若首段为 `workspace` 则取第二段)做 base,命中受保护名集合即整子树禁排。
 */
export function isProtectedPath(
  backendPath: string,
  protectedNames: ReadonlySet<string>,
): boolean {
  const segments = normalizePath(backendPath)
    .replace(/\\/g, '/')
    .split('/')
    .filter(Boolean);
  if (segments.length === 0) return false;
  const base = segments[0] === RESOURCE_ROOT_DIR ? segments[1] : segments[0];
  return Boolean(base) && protectedNames.has(base);
}

/** 便捷判定:给定资源条目是否处于受保护(不可排除)状态。 */
export function isProtectedResource(
  item: BotEditorResource,
  protectedNames: ReadonlySet<string>,
): boolean {
  return isProtectedPath(item.path, protectedNames);
}
