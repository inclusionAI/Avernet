import { FriendApprovalEditor } from '@/components/CollaborationPrivacy/FriendApprovalEditor';
import { IdentityCard } from '@/components/CollaborationPrivacy/IdentityCard';
import { PermissionCard } from '@/components/CollaborationPrivacy/PermissionCard';
import { PublicationEditor } from '@/components/CollaborationPrivacy/PublicationEditor';
import { ScopeViewer } from '@/components/CollaborationPrivacy/ScopeViewer';
import { PageHeader } from '@/components/Common/PageHeader';
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
  const overview = privacy.overview;
  return (
    <main className="h-full w-full overflow-y-auto">
      <div className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6 lg:p-8">
        <PageHeader
          title="协作权限"
          description="管理用户信息，以及归属于当前用户的所有 Bot 在 BCN 网络中的各类协作状态及好友审批策略。"
        />
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
