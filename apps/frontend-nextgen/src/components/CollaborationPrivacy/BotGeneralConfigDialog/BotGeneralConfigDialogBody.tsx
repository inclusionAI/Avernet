/**
 * BotWorkshop「公开设置」弹窗有状态主体（collab-permission-entry-migration）：
 * Bot管理列表行「更多」菜单入口，按单个 Bot 维度配置协作权限（发布审批 / Bot 可见性 / Bot 好友审批 / 协作能力）。
 * 数据面经 useCollaborationPrivacy({ fixedBotId }) 走 activeBot 按需加载；分组组件与协作权限页面共享。
 * 发布审批组仅 internal × 团队空间 Bot 渲染，并通过 lifecycle/approval OpenAPI 持久化；
 * 发布审批不依赖协作数据源，mine 失败/未命中时仍照常渲染（分区降级原则，AC-04~08）。
 * 内部 Bot 协作能力首行之后提供一次性修复入口；修复 Hook 只在主体挂载一次，
 * 不因协作数据 loading/error 切换丢失执行结果（AC-23~30）。
 */
import { getCapabilities } from '@/capabilities';
import { CollaborationRepairRow } from '@/components/CollaborationPrivacy/BotGeneralConfigDialog/CollaborationRepairRow';
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
import { useBotCollaborationRepair } from '@/hooks/useBotCollaborationRepair';
import { useBotPublishApproval } from '@/hooks/useBotPublishApproval';
import { useCollaborationPrivacy } from '@/hooks/useCollaborationPrivacy';
import type { UseHumanIdentityResult } from '@/hooks/useHumanIdentity';
import { getGeneralConfigAvailability, type BotDomain } from '@/services/botWorkshop';
import { history } from '@umijs/max';
import { Copy, RefreshCw, ShieldCheck, TriangleAlert } from 'lucide-react';
import { useMemo } from 'react';

export interface BotGeneralConfigDialogProps {
  /** BotWorkshop 列表行数据：身份三要素（id / 名称 / 引擎）+ 头像透传；外部 Bot PRD 预留同形复用。 */
  bot: BotDomain;
  /** 外部 Bot 仅配置对 Bot 可见性、好友审批及群聊参与。 */
  isExternalBot?: boolean;
  onClose: () => void;
}

interface BotGeneralConfigDialogBodyProps extends BotGeneralConfigDialogProps {
  /** 认证 Human 身份：修复归属判定与请求 userId 一律以当前登录人为准，不取当前 Bot 视角。 */
  human: UseHumanIdentityResult;
}

const codingBotAbilitySettings = ['collaborationStatus'] as const;
const generalConfigAbilitySettings = ['collaborationStatus', 'profilePublic'] as const;

/** 配置域卡片：一卡一域（发布审批/可见性/好友审批/协作能力），白底弹窗上细边框即分组。
 *  内边距对齐卡内行距节奏（RelationCard/SettingRow 的 py-3/py-4），卡口与行间留白一致。 */
export const sectionCardClass = 'rounded-xl border border-border/60 bg-card px-5 py-4';

function DialogLoadingState() {
  return (
    <div aria-label="正在加载公开设置" className={sectionCardClass}>
      <Skeleton.Card />
    </div>
  );
}

export function BotGeneralConfigDialogBody({ bot, isExternalBot = false, onClose, human }: BotGeneralConfigDialogBodyProps) {
  // 外部 Bot 由入口显式声明（绝不按 ID 猜测）：加载与可见性提交都走外部 PATCH 直改合同，无审批链路。
  const privacy = useCollaborationPrivacy({ fixedBotId: bot.id, isExternalBot });
  const targetBot = privacy.visibleBots[0];
  // Coding Bot 按 AgentCoding 模板能力裁剪协作能力；未声明 BCN 时整个分组不渲染。
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const showCollaborationAbilitySettings = !isAgentCodingBot || bot.runtime.bcnEnabled === true;
  // 外部 Bot 与 Coding Bot 仅展示群聊参与；其他 Bot 隐藏任务认领与 Dream Mode。
  const visibleAbilitySettings =
    isExternalBot || isAgentCodingBot ? codingBotAbilitySettings : generalConfigAbilitySettings;
  // 修复入口仅内部 Bot 且能力分组可见；外部 Bot / 未声明 BCN 的 Coding Bot 不出现。
  const userId = human.identity?.userId ?? '';
  const repairVisible = !isExternalBot && showCollaborationAbilitySettings;
  const availability = getGeneralConfigAvailability(
    bot,
    Boolean(userId && bot.ownerId === userId),
    bot.lock?.status === 'other',
  );
  const repairEnabled = repairVisible && human.status === 'ready' && availability.enabled;
  // 请求寻址使用 Backend 列表行 bot.id 与当前认证 Human，不使用 BCS targetBot.id。
  // 修复后的配置刷新走静默读取：只更新数据，不把整个「公开设置」切回加载骨架（刷新范围收敛到修复行自身的状态反馈）。
  const repair = useBotCollaborationRepair(bot.id, userId, repairEnabled, () =>
    privacy.load(undefined, { silent: true }),
  );
  const repairRow = repairVisible ? (
    <CollaborationRepairRow
      repair={repair}
      disabled={!repairEnabled}
      refreshFailed={Boolean(privacy.error)}
      disabledReason={availability.disabledReason}
    />
  ) : undefined;
  // 显示与复制均以协作域 Bot UUID 为准（含 :port 后缀，与协作权限页一致）；
  // 加载中/未命中时回退行数据 Bot ID（collab-permission-entry-migration 验收修正）。
  const displayId = targetBot?.id ?? bot.id;
  const showPublishApproval =
    getCapabilities().getGeneralConfigPublishApprovalEnabled().value && bot.spaceKind === 'team';
  // 分区仍仅团队空间渲染；开关仅服务 Bot 可用——个人 Bot 切到团队空间后无协作者语义，
  // 禁用置关并提示原因，且不发审批配置请求（后端对非服务 Bot 拒绝）。
  const publishApprovalEnabled = showPublishApproval && bot.serviceMode === 'service';
  const publishApproval = useBotPublishApproval(bot.id, bot.ownerId, publishApprovalEnabled);
  // 公开风险前置检测在途态（bot-public-risk-check）：仅「对用户」行消费；failed/attention 恢复编辑不在行上展示。
  const userRiskStatus = useMemo(() => {
    const { uiState, record } = privacy.userRisk;
    if (uiState.kind === 'checking' || uiState.kind === 'advancing' || uiState.kind === 'approving') {
      return {
        kind: uiState.kind,
        detailUrl: uiState.kind === 'approving' ? null : record?.detail_url ?? null,
        onViewApprovalProgress: () => history.push('/ticket-center'),
      };
    }
    return null;
  }, [privacy.userRisk]);
  const publishApprovalSection = showPublishApproval ? (
    <PublishApprovalSettings
      checked={publishApproval.required}
      disabled={!publishApprovalEnabled}
      notice={publishApprovalEnabled ? undefined : '个人 Bot 暂不支持协作者申请'}
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
          <ModalTitle>公开设置</ModalTitle>
          <ModalDescription>管理该 Bot 在 BCN 网络中的协作状态及好友审批策略。</ModalDescription>
        </ModalHeader>
        {/* 身份带：全弹窗唯一填充面（bg-muted/40），锚定「这是哪个 Bot 的配置」；左缘与下方分区卡片内容对齐。 */}
        <div className="flex min-w-0 items-center gap-2.5 rounded-xl bg-muted/40 px-5 py-3">
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
              title="公开设置加载失败"
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
                userRiskStatus={isExternalBot ? undefined : userRiskStatus}
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
                  grid={false}
                  visibleSettings={visibleAbilitySettings}
                  afterCollaborationStatus={repairRow}
                />
              </div>
            ) : null}
          </div>
        )}
        {/* 协作数据加载/失败/未命中时保留修复卡：修复不依赖 BCS 读取成功（AC-28），且不覆盖骨架。 */}
        {repairVisible && (privacy.loading || privacy.error || !targetBot) && (
          <div className={sectionCardClass}>
            <h4 className="mb-3 text-xs font-semibold text-muted-foreground">协作能力</h4>
            {repairRow}
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
            risk={isExternalBot ? undefined : privacy.userRisk}
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
