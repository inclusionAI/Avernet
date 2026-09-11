import { getCapabilities } from '@/capabilities';
import { FriendApprovalEditor } from '@/components/CollaborationPrivacy/FriendApprovalEditor';
import { IdentityCard } from '@/components/CollaborationPrivacy/IdentityCard';
import { PermissionCard } from '@/components/CollaborationPrivacy/PermissionCard';
import { PublicationEditor } from '@/components/CollaborationPrivacy/PublicationEditor';
import { ScopeViewer } from '@/components/CollaborationPrivacy/ScopeViewer';
import { PageHeader } from '@/components/Common/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Empty } from '@/components/ui/Empty';
import { Skeleton } from '@/components/ui/Skeleton';
import { useCollaborationPrivacy } from '@/hooks/useCollaborationPrivacy';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { RefreshCw, ShieldCheck } from 'lucide-react';

function LoadingState() {
  return (
    <div aria-label="正在加载协作权限" className="space-y-4">
      <Card className="p-5">
        <Skeleton.Line className="w-1/3" />
        <Skeleton.Line className="mt-3 w-2/3" />
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <Skeleton.Card />
        </Card>
        <Card>
          <Skeleton.Card />
        </Card>
      </div>
    </div>
  );
}

export default function CollaborationPrivacyPage() {
  const privacy = useCollaborationPrivacy();
  const { identity: accountIdentity } = useHumanIdentity();
  const userProfilePresentation = getCapabilities().getUserProfilePresentation().value;
  const overview = privacy.overview;
  return (
    <main className="h-full w-full overflow-y-auto">
      <div className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6 lg:p-8">
        <PageHeader
          title="协作权限"
          description="管理用户信息，以及归属于当前用户的所有 Bot 在 BCN 网络中的各类协作状态及好友审批策略"
        />
        {privacy.activeIdentity && !privacy.loading && !privacy.error && overview && (
          <Card className="border-primary/20 bg-primary/5">
            <div className="flex flex-wrap items-center gap-2 p-3 text-xs text-foreground">
              <span className="font-medium">当前工作身份：{privacy.activeIdentity.displayName}</span>
              <Badge tone={privacy.activeIdentity.kind === 'user' ? 'primary' : 'neutral'}>
                {privacy.activeIdentity.kind === 'user' ? '用户' : 'Bot'}
              </Badge>
              <span className="text-muted-foreground">
                {privacy.activeIdentity.kind === 'user'
                  ? '下方展示你的个人信息，切换至 Bot 身份可配置对应 Bot 的协作权限。'
                  : '下方展示该 Bot 的协作能力、可见性与好友审批配置；切换至用户身份可查看个人信息。'}
              </span>
              <span className="text-muted-foreground">请使用左上角工作身份切换。</span>
            </div>
          </Card>
        )}
        {privacy.loading && <LoadingState />}
        {!privacy.loading && privacy.error && (
          <Card>
            <Empty
              title="协作权限加载失败"
              description={privacy.error}
              icon={<ShieldCheck className="h-5 w-5" aria-hidden />}
              action={
                <Button leftIcon={<RefreshCw className="h-4 w-4" aria-hidden />} onClick={() => void privacy.load()}>
                  重新加载
                </Button>
              }
            />
          </Card>
        )}
        {!privacy.loading && !privacy.error && overview && (
          <>
            {privacy.showIdentityCard && (
              <IdentityCard
                identity={overview.currentUser}
                avatarUrl={accountIdentity?.avatarUrl}
                authenticatedIdentity={
                  userProfilePresentation.preferAuthenticatedUserProfile ? accountIdentity ?? undefined : undefined
                }
                showDepartment={userProfilePresentation.showDepartment}
                syncing={privacy.busyAction === 'syncDepartment'}
                onSync={() => void privacy.syncDepartment()}
              />
            )}
            {privacy.activeIdentity?.kind === 'bot' && (
              <>
                {privacy.visibleBots.length === 0 ? (
                  <Card>
                    <Empty title="暂无当前 Bot 的协作权限" description="当前 Bot 暂无可配置的协作权限内容。" />
                  </Card>
                ) : (
                  <div className="grid items-start gap-5">
                    {privacy.visibleBots.map((bot) => (
                      <PermissionCard
                        key={bot.id}
                        bot={bot}
                        busyAction={privacy.busyAction}
                        onCopyId={(botId) => void privacy.copyBotId(botId)}
                        onRefresh={(targetBot) => void privacy.refreshBot(targetBot)}
                        onToggleDirect={privacy.toggleDirect}
                        onEditPublication={privacy.openPublicationEditor}
                        onEditFriendApproval={privacy.openFriendEditor}
                        onViewScope={privacy.openScopeViewer}
                        onViewFriendApprovalScope={privacy.openFriendScopeViewer}
                      />
                    ))}
                  </div>
                )}
              </>
            )}
          </>
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
        {privacy.publicationEditor && privacy.publicationBot && overview && (
          <PublicationEditor
            open
            audience={privacy.publicationEditor.audience}
            initialConfig={privacy.publicationBot.publication[privacy.publicationEditor.audience]}
            onSearch={(keyword, signal) => privacy.searchDepartments(keyword, signal)}
            loading={
              privacy.busyAction === `${privacy.publicationBot.id}:publication:${privacy.publicationEditor.audience}`
            }
            onClose={privacy.closePublicationEditor}
            onSubmit={(config, deptEntries) => void privacy.submitPublication(config, deptEntries)}
          />
        )}
        {privacy.friendEditorBot && overview && (
          <FriendApprovalEditor
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
      </div>
    </main>
  );
}
