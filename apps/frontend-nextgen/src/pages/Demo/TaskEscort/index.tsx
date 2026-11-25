import { TaskEscortDemoPanel } from '@/components/Workspace/TaskEscortDemo';
import type { ChatHeaderPanelKind } from '@/pages/Workspace/Chat/hooks/useChatHeaderPanels';
import { useState } from 'react';
import { DemoContextPanel, type DemoContextPanelKind } from './DemoContextPanel';
import { DemoConversation } from './DemoConversation';
import { DEMO_BOT_NAME } from './demoContent';
import { DemoNavigation, DemoSessionSidebar } from './DemoNavigation';
import './styles.css';

export interface TaskEscortDemoPageProps {
  brandName?: string;
}

/** 独立路由宿主：不挂 AppLayout，不接身份、会话、消息或后端数据源。 */
export function TaskEscortDemoPage({ brandName = 'Avernet' }: TaskEscortDemoPageProps) {
  const [favorite, setFavorite] = useState(false);
  const [openPanel, setOpenPanel] = useState<ChatHeaderPanelKind | null>(null);
  const [fileDrawerOpen, setFileDrawerOpen] = useState(false);
  const [sidePaneOpen, setSidePaneOpen] = useState(false);

  const togglePanel = (kind: ChatHeaderPanelKind) => {
    const next = openPanel === kind ? null : kind;
    setOpenPanel(next);
    if (next !== null) setFileDrawerOpen(false);
    if (next === 'escort') setSidePaneOpen(false);
  };
  const openEscort = () => {
    setOpenPanel('escort');
    setFileDrawerOpen(false);
    setSidePaneOpen(false);
  };
  const toggleResources = () => {
    if (!fileDrawerOpen) {
      setOpenPanel(null);
      setSidePaneOpen(false);
    }
    setFileDrawerOpen(!fileDrawerOpen);
  };
  const toggleSidePane = () => {
    if (!sidePaneOpen) {
      setOpenPanel(null);
      setFileDrawerOpen(false);
    }
    setSidePaneOpen(!sidePaneOpen);
  };
  const contextPanel: DemoContextPanelKind | null =
    openPanel === 'detail' || openPanel === 'history'
      ? openPanel
      : fileDrawerOpen ? 'resources' : sidePaneOpen ? 'sidepane' : null;

  return (
    <div className="task-escort-demo-host bg-background text-foreground" data-demo="task-escort" data-escort-open={openPanel === 'escort'}>
      <DemoNavigation brandName={brandName} />
      <DemoSessionSidebar favorite={favorite} />
      <main className="task-escort-demo-chat" aria-label="任务护航演示聊天">
        <DemoConversation
          headerActions={{
            favorites: { favorite, disabled: false, onToggle: () => setFavorite((current) => !current) },
            openPanel,
            onTogglePanel: togglePanel,
            fileDrawerOpen,
            onToggleFileDrawer: toggleResources,
            sidePaneOpen,
            onToggleSidePane: toggleSidePane,
          }}
          onOpenEscort={openEscort}
        />
        {contextPanel && (
          <DemoContextPanel
            kind={contextPanel}
            onClose={() => {
              setOpenPanel(null);
              setFileDrawerOpen(false);
              setSidePaneOpen(false);
            }}
          />
        )}
      </main>
      {/* 保持挂载：close/open 只切显示，不清空内存中的演示流程。 */}
      <TaskEscortDemoPanel
        open={openPanel === 'escort'}
        onOpenChange={(open) => {
          if (!open) setOpenPanel(null);
        }}
        botName={DEMO_BOT_NAME}
      />
    </div>
  );
}

export default TaskEscortDemoPage;
