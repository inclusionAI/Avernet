/**
 * MobileHeader - 移动端 Header 组件库
 *
 * 组件列表：
 * - MobileHeader: 移动端顶部导航栏主组件
 * - TitleSwitcher: 标题切换器模板
 * - BotSwitcher: Bot 切换器（Header 中间）
 * - MarketSwitcher: 能力市场切换器（Header 中间）
 * - SettingTabsButton: 设置按钮（带 Tab 下拉菜单 + 抽屉）
 * - MobileNavDrawer: 左侧导航抽屉
 * - MobileListContainer: 移动端列表容器（支持横向滚动）
 * - MobileScrollList: 移动端横向滚动列表
 */

// 从 index.tsx 导出核心组件
export { BotSwitcher } from './BotSwitcher';
export {
  MobileHeader,
  TitleSwitcher,
  type TitleSwitcherProps,
} from './index.tsx';
export { MarketCategoryButton, MarketSwitcher } from './MarketSwitcher';
export { MobileListContainer, MobileScrollList } from './MobileListContainer';
export { MobileNavDrawer, type TabId } from './MobileNavDrawer';
export { SettingTabsButton, type SettingTab } from './SettingTabsButton';
