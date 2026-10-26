/**
 * BotWorkshop「通用配置」弹窗（collab-permission-entry-migration）：
 * Bot管理列表行「更多」菜单入口，按单个 Bot 维度配置协作权限（发布审批 / Bot 可见性 / Bot 好友审批 / 协作能力）。
 * 数据面经 useCollaborationPrivacy({ fixedBotId }) 走 activeBot 按需加载；分组组件与协作权限页面共享。
 * 发布审批组仅 internal × 团队空间 Bot 渲染，并通过 lifecycle/approval OpenAPI 持久化；
 * 发布审批不依赖协作数据源，mine 失败/未命中时仍照常渲染（分区降级原则，AC-04~08）。
 */
import { getCapabilities } from '@/capabilities';
import { FriendApprovalEditor } from '@/components/CollaborationPrivacy/FriendApprovalEditor';
import { PublicationEditor } from '@/components/CollaborationPrivacy/PublicationEditor';
import { ScopeViewer } from '@/components/CollaborationPrivacy/ScopeViewer';
import {
  BotAbilitySettings,
  BotVisibilitySettings,
  FriendApprovalSettings,
  PublishApprovalSettings,
} from '@/components/CollaborationPrivacy/settingsSections';
import { Avatar } from '@/components/ui/Avatar';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import { useBotPublishApproval } from '@/hooks/useBotPublishApproval';
import { useCollaborationPrivacy } from '@/hooks/useCollaborationPrivacy';
import type { BotDomain } from '@/services/botWorkshop';
import { Copy, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react';

interface BotGeneralConfigDialogProps {
  /** BotWorkshop 列表行数据：身份三要素（id / 名称 / 引擎）+ 头像透传；外部 Bot PRD 预留同形复用。 */
  bot: BotDomain;
  /** 外部 Bot 仅配置对 Bot 可见性、好友审批及群聊参与。 */
  isExternalBot?: boolean;
  onClose: () => void;
}

const codingBotAbilitySettings = ['collaborationStatus'] as const;
const generalConfigAbilitySettings = ['collaborationStatus', 'profilePublic'] as const;

/** 配置域卡片：一卡一域（发布审批/可见性/好友审批/协作能力），白底弹窗上细边框即分组。 */
const sectionCardClass = 'rounded-xl border border-border/60 bg-card px-4 py-3';

function DialogLoadingState() {
  return (
    <div aria-label="正在加载通用配置" className={sectionCardClass}>
      <Skeleton.Card />
    </div>
  );
}

export function BotGeneralConfigDialog({ bot, isExternalBot = false, onClose }: BotGeneralConfigDialogProps) {
  // 外部 Bot 由入口显式声明（绝不按 ID 猜测）：加载与可见性提交都走外部 PATCH 直改合同，无审批链路。
  const privacy = useCollaborationPrivacy({ fixedBotId: bot.id, isExternalBot });
  const targetBot = privacy.visibleBots[0];
  // Coding Bot 按 AgentCoding 模板能力裁剪协作能力；未声明 BCN 时整个分组不渲染。
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const showCollaborationAbilitySettings = !isAgentCodingBot || bot.runtime.bcnEnabled === true;
  // 外部 Bot 与 Coding Bot 仅展示群聊参与；其他 Bot 隐藏任务认领与 Dream Mode。
  const visibleAbilitySettings = isExternalBot || isAgentCodingBot ? codingBotAbilitySettings : generalConfigAbilitySettings;
  // 显示与复制均以协作域 Bot UUID 为准（含 :port 后缀，与协作权限页一致）；
  // 加载中/未命中时回退行数据 Bot ID（collab-permission-entry-migration 验收修正）。
  const displayId = targetBot?.id ?? bot.id;
  const showPublishApproval =
    getCapabilities().getGeneralConfigPublishApprovalEnabled().value && bot.spaceKind === 'team';
  const publishApproval = useBotPublishApproval(bot.id, bot.ownerId, showPublishApproval);
  const publishApprovalSection = showPublishApproval ? (
    <PublishApprovalSettings
      checked={publishApproval.required}
      loading={publishApproval.loading || publishApproval.updating}
      error={publishApproval.error}
      onChange={(checked) => void publishApproval.update(checked)}
    />
  ) : null;
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <ModalContent size="xl">
        <ModalHeader>
          <ModalTitle>通用配置</ModalTitle>
          <ModalDescription>管理该 Bot 在 BCN 网络中的协作状态及好友审批策略。</ModalDescription>
        </ModalHeader>
        {/* 身份带：全弹窗唯一填充面（bg-muted/40），锚定「这是哪个 Bot 的配置」。 */}
        <div className="flex min-w-0 items-center gap-2.5 rounded-xl bg-muted/40 px-4 py-3">
          <Avatar name={bot.name} src={bot.avatarUrl} size={40} />
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 truncate text-sm font-medium text-foreground">{bot.name}</span>
              {bot.runtime.engine !== 'unknown' && <Badge tone="neutral">{bot.runtime.engine}</Badge>}
            </div>
            <div className="mt-1 flex min-w-0 items-center gap-2">
              <span className="shrink-0 text-xs text-muted-foreground">Bot UUID</span>
              <code className="min-w-0 max-w-[28rem] truncate rounded-md bg-card px-1.5 py-0.5 text-xs text-foreground">
                {displayId}
              </code>
              {/* 验收修正：IconButton 内置 Tooltip 会在弹窗打开时被指针位置误触发，
                  复制按钮改用无 Tooltip 的 Button 图标形态（可访问名称不变）。 */}
              <Button
                variant="ghost"
                size="icon"
                className="size-7 shrink-0"
                aria-label={`复制 ${bot.name} 的 Bot UUID`}
                onClick={() => void privacy.copyBotId(displayId)}
              >
                <Copy className="size-3.5" aria-hidden />
              </Button>
            </div>
          </div>
        </div>
        {publishApprovalSection ? <div className={sectionCardClass}>{publishApprovalSection}</div> : null}
        {privacy.loading ? (
          <DialogLoadingState />
        ) : privacy.error ? (
          <div className={sectionCardClass}>
            <Empty
              title="通用配置加载失败"
              description={privacy.error}
              icon={<ShieldCheck className="h-5 w-5" aria-hidden />}
              action={
                <Button leftIcon={<RefreshCw className="h-4 w-4" aria-hidden />} onClick={() => void privacy.load()}>
                  重试
                </Button>
              }
            />
          </div>
        ) : !targetBot ? (
          <div className={sectionCardClass}>
            <Empty
              title="未获取到该 Bot 的协作配置"
              description="该 Bot 可能尚未加入 BCN 协作网络；可稍后重试。"
              icon={<ShieldCheck className="h-5 w-5" aria-hidden />}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {!targetBot.joinedBcn && (
              <div className="flex items-center gap-2 rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
                <TriangleAlert className="size-4 shrink-0" aria-hidden />
                加入 BCN 后才能修改协作权限
              </div>
            )}
            <div className={sectionCardClass}>
              <BotVisibilitySettings
                bot={targetBot}
                showUserVisibility={!isExternalBot}
                isExternalBot={isExternalBot}
                onEditPublication={privacy.openPublicationEditor}
                onViewScope={privacy.openScopeViewer}
              />
            </div>
            <div className={sectionCardClass}>
              <FriendApprovalSettings
                bot={targetBot}
                onEditFriendApproval={privacy.openFriendEditor}
                onViewFriendApprovalScope={privacy.openFriendScopeViewer}
              />
            </div>
            {showCollaborationAbilitySettings ? (
              <div className={sectionCardClass}>
                <BotAbilitySettings
                  bot={targetBot}
                  busyAction={privacy.busyAction}
                  onToggleDirect={privacy.toggleDirect}
                  grid
                  visibleSettings={visibleAbilitySettings}
                />
              </div>
            ) : null}
          </div>
        )}
        <ConfirmDialog
          open={Boolean(privacy.confirmation)}
          title={privacy.confirmation?.title ?? ''}
          description={privacy.confirmation?.description ?? ''}
          loading={Boolean(
            privacy.confirmation &&
              privacy.busyAction === `${privacy.confirmation.bot.id}:${privacy.confirmation.setting}`,
          )}
          confirmVariant={
            privacy.confirmation?.value === false || privacy.confirmation?.value === 'hidden'
              ? 'destructive'
              : 'primary'
          }
          onCancel={privacy.cancelConfirmation}
          onConfirm={() => void privacy.confirmDirect()}
        />
        {privacy.publicationEditor && privacy.publicationBot && privacy.overview && (
          <PublicationEditor
            open
            audience={privacy.publicationEditor.audience}
            isExternalBot={isExternalBot}
            initialConfig={privacy.publicationBot.publication[privacy.publicationEditor.audience]}
            onSearch={(keyword, signal) => privacy.searchDepartments(keyword, signal)}
            loading={
              privacy.busyAction === `${privacy.publicationBot.id}:publication:${privacy.publicationEditor.audience}`
            }
            onClose={privacy.closePublicationEditor}
            onSubmit={(config, deptEntries) => void privacy.submitPublication(config, deptEntries)}
          />
        )}
        {privacy.friendEditorBot && privacy.overview && (
          <FriendApprovalEditor
            desktop={privacy.friendEditorBot.desktop}
            open
            initialConfig={privacy.friendEditorBot.friendApproval}
            onSearch={(keyword, signal) => privacy.searchDepartments(keyword, signal)}
            loading={privacy.busyAction === `${privacy.friendEditorBot.id}:friendApproval`}
            onClose={privacy.closeFriendEditor}
            onSubmit={(config) => void privacy.submitFriendApproval(config)}
          />
        )}
        {privacy.scopeViewer &&
          privacy.scopeViewerBot &&
          (privacy.scopeViewer.kind === 'publication' ? (
            <ScopeViewer
              open
              kind="publication"
              audience={privacy.scopeViewer.audience}
              config={privacy.scopeViewerBot.publication[privacy.scopeViewer.audience]}
              onClose={privacy.closeScopeViewer}
            />
          ) : (
            <ScopeViewer
              open
              kind="friendApproval"
              config={privacy.scopeViewerBot.friendApproval}
              onClose={privacy.closeScopeViewer}
            />
          ))}
      </ModalContent>
    </Modal>
  );
}
