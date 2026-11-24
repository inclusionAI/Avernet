// 侧栏底部用户区（展开语义，SVG 稿）：32px 圆头像 + 14px 姓名 + 右侧「新版助手」与「更多」。
// 「返回旧版」常驻入口（灰度切流，capability 门控）置用户行上方：Open Core 无旧版形态判定
// capability null，组件渲染 null，布局零变化。
// 由 ≥lg 内流侧栏（AppSidebar 展开态）与 <lg 导航抽屉（AppShell）复用，保证两处能力一致。
import { getCapabilities } from '@/capabilities';
import { AccountBadge, type AccountUser } from './AccountBadge';
import { HelpMenu } from './HelpMenu';
import { LegacyVersionSwitch } from './LegacyVersionSwitch';

interface SidebarUserFooterProps {
  currentUser?: AccountUser | null;
  /**
   * <lg 抽屉场景传入：新版助手浮层为流内 fixed（与 Drawer 同取 --z-drawer，会被更晚挂载的
   * Dialog portal 压住），点击入口后需先收起抽屉，面板退出抽屉遮挡后方可全屏可见。
   */
  onGuideEntryIntent?: () => void;
}

/** 底部用户区：与导航区之间无分隔线（稿为纯留白过渡）。 */
export function SidebarUserFooter({ currentUser, onGuideEntryIntent }: SidebarUserFooterProps) {
  // 产品引导（新版助手）常驻入口：capability 门控（Open Core null → 不渲染，布局零变化，
  // 与 LegacyVersionSwitch 同款模式）
  const guideAssistant = getCapabilities().getGuideAssistant();
  const GuideEntry = guideAssistant.status === 'available' ? guideAssistant.value?.Entry : undefined;

  return (
    <div className="mt-auto space-y-0.5 bg-background/70 px-2 pb-3 pt-2">
      <LegacyVersionSwitch />
      <div className="flex items-center gap-1">
        <AccountBadge currentUser={currentUser} />
        <span className="ml-auto flex shrink-0 items-center">
          {GuideEntry ? (
            <span className="flex" onClick={onGuideEntryIntent}>
              <GuideEntry />
            </span>
          ) : null}
          <HelpMenu variant="more" />
        </span>
      </div>
    </div>
  );
}
