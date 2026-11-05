/** ConversationBotItemLayout —— Bot 行显隐与让位布局的纯计算（自 ConversationBotItem
 * 拆出：TC-G005 行数门禁）。2026-11-04 显隐重构：操作组仅 hover/键盘/菜单打开/触屏
 * 显示（触屏常显→让位同步常显），展开态不再整组钉住；激活标记（🔍 检索/实例、归属/
 * 范围筛选非默认项）常显并按数量预留静态让位。 */
import type { ConversationOrigin, ConversationSessionScope } from '@/domain/conversation/types';

export interface BotRowLayoutInputs {
  isManaged: boolean;
  /** 管理视角的只读分组（others：无操作组按钮、无菜单）。 */
  isOthers: boolean;
  canFavorite: boolean;
  origin: ConversationOrigin;
  scope: ConversationSessionScope;
  /** 已生效的会话检索条件（空串 = 未生效）。 */
  sessionKey: string;
  expanded: boolean;
  unavailable: boolean;
  hasSessionControls: boolean;
  /** 实例范围聚合行标记（🔍 检索/实例命中时计入常显让位）。 */
  searchMarked: boolean;
  /** 「Bot 管理」菜单入口条件成立（回调存在且 bot.spaceId 有值）。 */
  hasManagement: boolean;
}

export interface BotRowLayout {
  filterActive: boolean;
  searchActive: boolean;
  /** 搜索入口仅在有效范围 all 下展示：favorite 清空并隐藏（切回 all 不恢复），others 强制 all 保留。 */
  searchVisible: boolean;
  showFilter: boolean;
  showMenu: boolean;
  /** 操作区渲染门控：可用行走既有会话入口；不可用 Bot 仅保留常驻「更多操作」（运维直达）。 */
  stripVisible: boolean;
  /** 操作组 hover 让位的三档右侧预留（86/112/138px）。 */
  hoverActionsPadding: string;
  pinnedMarkCount: number;
  pinnedMarked: boolean;
  pinnedMarkPadding: string;
}

export function resolveBotRowLayout(inputs: BotRowLayoutInputs): BotRowLayout {
  const {
    isManaged,
    isOthers,
    canFavorite,
    origin,
    scope,
    sessionKey,
    expanded,
    unavailable,
    hasSessionControls,
    searchMarked,
    hasManagement,
  } = inputs;
  // 筛选/搜索的激活与可见:归属 others 或 favorite 范围点亮筛选;归属+范围筛选对管理 Bot 开放,
  // 好友 Bot 仅在有收藏能力时暴露「会话」范围(TEClaw 无收藏接口,不发收藏请求)。
  const filterActive = (isManaged && origin === 'others') || (canFavorite && scope === 'favorite');
  const searchActive = sessionKey !== '';
  const searchVisible = scope === 'all';
  const showFilter = isManaged || canFavorite;
  const showMenu = hasManagement && !isOthers;
  const stripVisible = (hasSessionControls && !unavailable) || (unavailable && showMenu);
  // 展开态的收起入口并入右侧操作组。发布分支已将渐隐区收紧 10px：
  // 3/4/5 个按钮分别预留 86/112/138px（按钮 24px + 间隙 2px）。
  const actionButtonCount =
    (expanded ? 1 : 0) + (!isOthers ? 1 : 0) + (searchVisible ? 1 : 0) + (showFilter ? 1 : 0) + (showMenu ? 1 : 0);
  // hover 让位(2026-11-04 显隐重构):操作组仅 hover/键盘/菜单打开/触屏显示(触屏常显
  // → 让位同步常显),展开态不再整组钉住;激活 icon 让位见 pinnedMarkPadding。
  // 展开+菜单达 5 按钮时 hover 让位需 138px(原 pinned 独有档位下沉到 hover 变体)。
  const hoverActionsPadding =
    actionButtonCount >= 5
      ? 'group-hover:pr-[138px] group-focus-within:pr-[138px] group-has-[[data-state=open]]:pr-[138px] [@media(hover:none)]:pr-[138px]'
      : actionButtonCount >= 4
        ? 'group-hover:pr-[112px] group-focus-within:pr-[112px] group-has-[[data-state=open]]:pr-[112px] [@media(hover:none)]:pr-[112px]'
        : 'group-hover:pr-[86px] group-focus-within:pr-[86px] [@media(hover:none)]:pr-[86px]';
  // 常显 icon 让位只保留真正常驻的激活标记(🔍 检索/实例、归属/范围筛选非默认项)。
  // 2026-11-05 回归修正:展开态收起按钮是 hover 操作,不再计入静态让位;若仍常驻会让
  // Bot 行静默态保留一块右侧空位,并连带隐藏徽章。
  const pinnedMarkCount = (searchMarked && searchVisible ? 1 : 0) + (filterActive ? 1 : 0);
  const pinnedMarked = !unavailable && pinnedMarkCount > 0;
  const pinnedMarkPadding = pinnedMarkCount >= 3 ? 'pr-[86px]' : pinnedMarkCount >= 2 ? 'pr-[60px]' : 'pr-9';
  return {
    filterActive,
    searchActive,
    searchVisible,
    showFilter,
    showMenu,
    stripVisible,
    hoverActionsPadding,
    pinnedMarkCount,
    pinnedMarked,
    pinnedMarkPadding,
  };
}
