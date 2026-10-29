import { ExpertCreationDetails } from '@/components/BotWorkshop/ExpertCreationDetails';
import { useBotEditorAddress } from './useBotEditorAddress';
import { useBotEditorPanels } from './useBotEditorPanels';
import { DetailDebugChat } from './DetailDebugChat';
import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Spin } from '@/components/ui/Spin';
import { isSameHumanIdentity } from '@/domain/userIdentity';
import { useBotAdvancedConfig } from '@/hooks/useBotAdvancedConfig';
import { useBotEditor } from '@/hooks/useBotEditor';
import { useBotWorkshopDetail } from '@/hooks/useBotWorkshopDetail';
import { useBotWorkshopEditorIdentity } from '@/hooks/useBotWorkshopEditorIdentity';
import { useDesktopFolder } from '@/hooks/useDesktopFolder';
import { useSpaceContext } from '@/hooks/useSpaceContext';
import { isServiceVerification } from '@/services/botWorkshop/botPolicy';
import { desktopCapabilityPolicy } from '@/services/botWorkshop/desktopCapabilityPolicy';
import { cn } from '@/utils/cn';
import React, { useEffect, useState } from 'react';
import { DetailHeader } from './DetailHeader';
import { DetailNavigation } from './DetailNavigation';
import { DetailPanelContent } from './DetailPanelContent';
import { moreTabs } from './detailTabs';
import { useHostingTabs } from './useHostingTabs';
import { Points } from '@/tracking/points';import { Tracert } from '@/utils/tracert';
const BotWorkshopDetailPage: React.FC = () => {
  const { id, ownerId, requestedEditable, runtimeStage } = useBotEditorAddress();
  const openDesktopFolder = useDesktopFolder(id);
  const currentSpaceId = useSpaceContext((state) => state.currentSpaceId);
  const requestIdentity = useBotWorkshopEditorIdentity();
  const detail = useBotWorkshopDetail(id, requestedEditable, requestIdentity.ready, ownerId, requestIdentity.userId);
  const verifying =
    !!detail.bot &&
    detail.bot.serviceMode === 'service' &&
    (runtimeStage === 'verify' || isServiceVerification(detail.bot));
  const editable = requestedEditable && !verifying;
  const addressedOwnerId = ownerId ?? detail.bot?.ownerId;
  const isOwner = isSameHumanIdentity(addressedOwnerId, requestIdentity.userId);
  const editor = useBotEditor(
    id,
    detail.bot?.serviceMode === 'service',
    currentSpaceId === undefined ? undefined : String(currentSpaceId),
    requestIdentity.ready && detail.bot?.id === id,
    addressedOwnerId,
    isOwner,
    detail.bot?.deployment,
    detail.bot?.runtime.engine,
  );
  const desktopPolicy = desktopCapabilityPolicy(detail.bot?.deployment ?? '', detail.bot?.runtime.engine ?? '');
  const advanced = useBotAdvancedConfig(
    id,
    requestIdentity.ready && !!detail.bot,
    ownerId,
    desktopPolicy.markdown,
    desktopPolicy.channels,
  );
  const { panels, additionalTabs } = useBotEditorPanels();
  const { tab, setTab, more, setMore, hostingAllowed, visibleMainTabs } = useHostingTabs(
    id,
    requestIdentity.userId,
    detail.bot,
    isOwner,
    desktopPolicy.routines,
    additionalTabs,
    JSON.stringify([addressedOwnerId, runtimeStage, currentSpaceId]),
  );
  const ExtensionPanel = panels.find((panel) => `extension:${panel.id}` === tab)?.Panel;
  const [debugOpen, setDebugOpen] = useState(true); const [debugFullscreen, setDebugFullscreen] = useState(false);
  useEffect(() => {
    setDebugOpen(true);
    setDebugFullscreen(false);
  }, [id]);
  if (!id)
    return (
      <Empty
        title="缺少 Bot 标识"
        description="请从 Bot 管理重新进入。"
        action={<Button onClick={detail.back}>返回 Bot 管理</Button>}
      />
    );
  if (requestIdentity.loading) return <Spin tip="正在获取当前用户身份…" />;
  if (requestIdentity.error)
    return (
      <Empty
        title="无法加载用户身份"
        description={requestIdentity.error}
        action={<Button onClick={detail.back}>返回 Bot 管理</Button>}
      />
    );
  if (detail.loading) return <Spin tip="加载 Bot 配置…" />;
  if (detail.error || !detail.bot)
    return (
      <Empty
        title="无法查看 Bot"
        description={detail.error ?? 'Bot 不存在或无权访问'}
        action={<Button onClick={detail.back}>返回 Bot 管理</Button>}
      />
    );
  const bot = detail.bot;
  const visibleMoreTabs = moreTabs.filter(
    (item) =>
      ({
        engine: desktopPolicy.engineConfig && isOwner,
        md: desktopPolicy.markdown,
        node: desktopPolicy.nodes,
        channel: desktopPolicy.channels,
        screen: desktopPolicy.screens,
      }[item.key]),
  );
  return (
    // 左右分栏:右侧调试对话整列到顶(与 header 同一起点);"调试对话/保存"按钮
    // 随 DetailHeader 只占据左侧标题区,天然位于该区域最右端。
    <main className="bot-editor-shell flex h-full min-h-0 bg-background">
      <div
        className={cn(
          'flex h-full min-w-0 flex-1 flex-col',
          debugFullscreen && 'hidden',
        )}
      >
        <DetailHeader
          bot={bot}
          editable={editable}
          isOwner={isOwner}
          debugOpen={debugOpen}
          saving={detail.saving}
          onBack={detail.back}
          onOpenDebug={() => setDebugOpen(true)}
          onSaveBasic={async (input) => {
            Tracert.click(Points.botWorkshop.saveConfig, { bot_id: id }); await detail.save(input);
          }}
          onReload={() => void detail.retry()}
        />
        {isOwner && !verifying && bot.expertOriginName && <ExpertCreationDetails key={bot.id} botId={bot.id} />}
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <DetailNavigation
            hidden={debugFullscreen}
            mainTabs={visibleMainTabs}
            moreTabs={visibleMoreTabs}
            activeTab={tab}
            activeMore={more}
            onTabChange={(nextTab) => {
              setTab(nextTab);
              setMore(undefined);
            }}
            onMoreChange={(nextMore) => {
              setMore(nextMore);
              if (nextMore === 'engine') void editor.loadEngineConfig();
            }}
          />
          <section
            className={cn(
              'app-scrollbar min-w-0 flex-1 overflow-y-auto bg-background lg:max-w-none',
              // 去掉旧版的 lg:w-[56%] 定宽:flex-1 撑满左列,面板 header/内容右缘
              // 才能与上方 bot 名称所在 header 对齐;右描边仅在对话区可见的 lg+ 生效。
              debugOpen && !debugFullscreen && 'lg:border-r lg:border-border',
              debugFullscreen && 'hidden',
            )}
          >
            {verifying ? (
              <p className="border-b border-border bg-muted px-6 py-3 text-sm text-muted-foreground">
                预发验证中，配置已锁定；请在右侧对话区验证 Bot。
              </p>
            ) : null}
            <fieldset
              disabled={verifying}
              // h-full 定高:Chromium 下 fieldset 内的百分比高度(min-h-full)不生效,
              // 不给 fieldset 定高时面板内容不到底、分割线接不了页底(headless 实测 fieldset 内 min-height:100% 解析为 auto)。
              className={cn('h-full min-w-0 border-0 p-0', verifying && 'pointer-events-none opacity-50')}
            >
              <DetailPanelContent
                bot={bot}
                editor={editor}
                advanced={advanced}
                desktopPolicy={desktopPolicy}
                more={more}
                tab={tab}
                hostingAllowed={hostingAllowed}
                editable={editable}
                isOwner={isOwner}
                compact={debugOpen && !debugFullscreen}
                ExtensionPanel={ExtensionPanel}
                currentSpaceId={currentSpaceId}
                runtimeStage={runtimeStage}
                ownerId={addressedOwnerId}
                onOpenFolder={openDesktopFolder}
              />
            </fieldset>
          </section>
          </div>
      </div>
      {debugOpen ? (
        <DetailDebugChat
          authenticatedUserId={requestIdentity.ready ? requestIdentity.userId ?? null : null}
          key={`${bot.id}:${verifying ? 'verify' : runtimeStage ?? bot.lifecycle}`}
          bot={bot}
          runtimeStage={verifying ? 'verify' : runtimeStage}
          ownerId={addressedOwnerId}
          fullscreen={debugFullscreen}
          onClose={() => {
            setDebugOpen(false);
            setDebugFullscreen(false);
          }}
          onToggleFullscreen={() => setDebugFullscreen((current) => !current)}
        />
      ) : null}
    </main>
  );
};
export default BotWorkshopDetailPage;
