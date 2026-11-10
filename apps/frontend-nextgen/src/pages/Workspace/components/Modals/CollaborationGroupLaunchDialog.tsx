import { resolveBotAvatarUrl } from '@/domain/botAvatar';
import { Button } from '@/components/ui';
import { Modal, ModalContent } from '@/components/ui/Modal';
import type { IdentityView } from '@/domain/collaboration';
import type { CollaborationDraft, CollaborationDraftReference } from '@/domain/collaborationDraft';
import { navigateToLaunchedGroup } from '@/services/workspace/collaborationGroupLaunchNavigation';
import {
  useCollaborationGroupLaunchStore,
  type CollaborationGroupLaunchRequest,
} from '@/stores/collaborationGroupLaunchStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useState } from 'react';
import { useCollaborationDraftLaunch } from '../../hooks/useCollaborationDraftLaunch';
import { useDraftGroupSubmission } from '../../hooks/useDraftGroupSubmission';
import { CreateGroupHeader } from './CreateGroupHeader';
import { CreateGroupModal } from './CreateGroupModal';
import { DraftGroupForm, type DraftGroupValues } from './DraftGroupForm';
import { GroupLeaderSelect } from './GroupLeaderSelect';

const close = () => useCollaborationGroupLaunchStore.getState().close();

function LoadedDraftDialog({ draft, bots }: { draft: CollaborationDraft; bots: IdentityView[] }) {
  const activeId = useWorkspaceStore((state) => state.activeIdentityId);
  const [originatorId, setOriginatorId] = useState(
    () => bots.find((bot) => bot.id === activeId)?.id ?? (bots.length === 1 ? bots[0].id : ''),
  );
  const [values, setValues] = useState<DraftGroupValues>({
    name: draft.title ?? '',
    context: '',
    yaml: draft.definitionYaml,
  });
  const submission = useDraftGroupSubmission();
  const originator = bots.find((bot) => bot.id === originatorId);
  return (
    <Modal open onOpenChange={(open) => !open && close()}>
      <ModalContent
        size="lg"
        closeLabel="关闭发起协作弹窗"
        showClose={!submission.creating}
        className="h-dvh max-h-dvh w-full max-w-[720px] min-w-0 grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden rounded-none border-0 p-0 sm:h-[min(900px,calc(100dvh-3rem))] sm:w-[calc(100vw-3rem)] sm:rounded-2xl sm:border [&:has([data-flow-aside])]:xl:max-w-[1320px] [&:has([data-flow-aside])]:xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]"
      >
        <CreateGroupHeader />
        <DraftGroupForm
          key={originatorId}
          originator={originator}
          values={values}
          onChange={setValues}
          {...submission}
          onSubmit={submission.submit}
          onClose={close}
          originatorSlot={
            bots.length ? (
              <GroupLeaderSelect
                id="draft-group-originator"
                label="发起 Bot"
                value={originatorId}
                placeholder="选择我的 Bot"
                onChange={(id) => {
                  if (!submission.creating && !submission.blocked) setOriginatorId(id);
                }}
                options={bots.map((bot) => ({
                  id: bot.id,
                  name: bot.displayName,
                  avatarUrl: resolveBotAvatarUrl(bot.avatarUrl, bot.id),
                  current: bot.id === activeId,
                  description: `${bot.online ? '在线' : '离线'} · ${bot.id}`,
                }))}
              />
            ) : (
              <p className="text-xs text-muted-foreground">暂无可发起协作的自有 Bot，请先创建或绑定 Bot。</p>
            )
          }
        />
      </ModalContent>
    </Modal>
  );
}

function DraftDialog({ reference }: { reference: CollaborationDraftReference }) {
  const { data, error, retry } = useCollaborationDraftLaunch(reference);
  if (data) return <LoadedDraftDialog {...data} />;
  return (
    <Modal open onOpenChange={(open) => !open && close()}>
      <ModalContent size="lg" className="gap-0 p-0">
        <CreateGroupHeader />
        <div className="space-y-3 p-6">
          {error ? (
            <>
              <p role="alert" className="text-xs text-destructive">
                {error}
              </p>
              <Button onClick={retry}>重试</Button>
            </>
          ) : (
            <p role="status" className="text-xs text-muted-foreground">
              正在读取协作版本和自有 Bot…
            </p>
          )}
        </div>
      </ModalContent>
    </Modal>
  );
}

export default function CollaborationGroupLaunchDialog({ request }: { request: CollaborationGroupLaunchRequest }) {
  const active = useWorkspaceStore((state) =>
    state.identities.find((identity) => identity.id === state.activeIdentityId),
  );
  if (request.options.draft) return <DraftDialog reference={request.options.draft} />;
  return (
    <CreateGroupModal
      open
      activeIdentity={active}
      onClose={close}
      onCreated={(group) => {
        close();
        navigateToLaunchedGroup({
          groupId: group.groupId,
          driverBotId: group.driverBotUuid ?? active?.id ?? '',
          initialSessionId: group.initialSessionId,
        });
      }}
    />
  );
}
