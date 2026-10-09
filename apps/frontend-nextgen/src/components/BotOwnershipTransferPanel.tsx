import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import {
  botAuthorityService,
  type BotManagerView,
  type BotOwnershipState,
  type BotOwnershipTransferView,
} from '@/services/workspace/botAuthorityService';
import { useCallback, useEffect, useState } from 'react';

export type BotOwnershipPanelMode = 'bot' | 'human';

export interface BotOwnershipTransferPanelProps {
  mode: BotOwnershipPanelMode;
  /** Bot 视角必填：mine 的 bot_id。 */
  botId?: string;
  /**
   * 当前身份对该 Bot 的 access_relation（来自 mine，owner|manager）。
   * 前端不自行推断当前 owner：无 access_relation 时按无权限展示。
   */
  accessRelation?: 'owner' | 'manager';
}

const TRANSFER_STATUS_LABEL: Record<BotOwnershipTransferView['status'], string> = {
  pending: '待确认',
  accepted: '已接受',
  rejected: '已拒绝',
  cancelled: '已取消',
  expired: '已过期',
  invalidated: '已失效',
};

/**
 * 仅转移 BCS ownership 的三条边界（spec §11.1：acceptance UI 必须展示）。
 */
export function OwnershipScopeStatement() {
  return (
    <div className="rounded-lg bg-muted/40 px-3 py-2 text-xs leading-5 text-muted-foreground">
      <p>仅转移 BCS ownership：原 owner 保留 manager；部署与凭据不迁移。</p>
    </div>
  );
}

function TransferStatusBadge({ status }: { status: BotOwnershipTransferView['status'] }) {
  return (
    <span className="text-xs text-muted-foreground">
      {TRANSFER_STATUS_LABEL[status] ?? status}
    </span>
  );
}

/** 收/发件一行：receipt 历史快照信息（不展示 current owner）。 */
function TransferItem({
  transfer,
  direction,
  acting,
  onAccept,
  onReject,
  onCancel,
}: {
  transfer: BotOwnershipTransferView;
  direction: 'received' | 'sent';
  acting: boolean;
  onAccept: (transferId: string) => void;
  onReject: (transferId: string) => void;
  onCancel: (transferId: string) => void;
}) {
  const counterpart =
    direction === 'received' ? `来自 ${transfer.fromUserId}` : `发给 ${transfer.toUserId}`;
  return (
    <li className="flex items-center justify-between gap-3 py-2 text-sm">
      <span className="min-w-0 flex-1 truncate">
        {transfer.botNameSnapshot || transfer.botId}
        <span className="ml-2 text-xs text-muted-foreground">{counterpart}</span>
      </span>
      <TransferStatusBadge status={transfer.status} />
      {transfer.status === 'pending' && direction === 'received' && (
        <span className="flex items-center gap-2">
          <Button
            size="sm"
            loading={acting}
            onClick={() => onAccept(transfer.transferId)}
          >
            确认接受
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={acting}
            onClick={() => onReject(transfer.transferId)}
          >
            拒绝转交
          </Button>
        </span>
      )}
      {transfer.status === 'pending' && direction === 'sent' && (
        <Button
          size="sm"
          variant="secondary"
          loading={acting}
          onClick={() => onCancel(transfer.transferId)}
        >
          取消转交
        </Button>
      )}
    </li>
  );
}

/** Human 视角：转交收发件（收到的可确认/拒绝；发出的可取消）。 */
function HumanTransferInbox() {
  const [received, setReceived] = useState<BotOwnershipTransferView[]>([]);
  const [sent, setSent] = useState<BotOwnershipTransferView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [receivedResult, sentResult] = await Promise.all([
      botAuthorityService.listTransfers('received', { offset: 0, limit: 20 }),
      botAuthorityService.listTransfers('sent', { offset: 0, limit: 20 }),
    ]);
    let firstError: string | null = null;
    if (receivedResult.ok) {
      setReceived(receivedResult.data);
    } else {
      firstError = receivedResult.error.friendlyMessage;
    }
    if (sentResult.ok) {
      setSent(sentResult.data);
    } else {
      firstError = firstError ?? sentResult.error.friendlyMessage;
    }
    if (firstError) setError(firstError);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const replaceTransfer = (
    list: BotOwnershipTransferView[],
    transferId: string,
    result?: BotOwnershipTransferView,
  ): BotOwnershipTransferView[] =>
    list.map((item) => (item.transferId === transferId && result ? result : item));

  const decide = useCallback(
    async (
      action: 'accept' | 'reject' | 'cancel',
      transferId: string,
    ) => {
      if (acting) return;
      setActing(true);
      const result =
        action === 'accept'
          ? await botAuthorityService.acceptTransfer(transferId)
          : action === 'reject'
          ? await botAuthorityService.rejectTransfer(transferId)
          : await botAuthorityService.cancelTransfer(transferId);
      if (result.ok) {
        setReceived((current) => replaceTransfer(current, transferId, result.data));
        setSent((current) => replaceTransfer(current, transferId, result.data));
      } else {
        setError(result.error.friendlyMessage);
      }
      setActing(false);
    },
    [acting],
  );

  return (
    <Card className="p-5">
      <h3 className="text-sm font-semibold text-foreground">BCS ownership 转交收发件</h3>
      <div className="mt-2">
        <OwnershipScopeStatement />
      </div>
      {loading ? (
        <p className="mt-3 text-sm text-muted-foreground">正在加载转交收发件</p>
      ) : (
        <>
          {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
          <div className="mt-3 grid gap-4 xl:grid-cols-2">
            <section>
              <h4 className="text-xs font-semibold text-muted-foreground">收到的转交</h4>
              {received.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">暂无收到的转交。</p>
              ) : (
                <ul className="mt-1 divide-y divide-border">
                  {received.map((transfer) => (
                    <TransferItem
                      key={transfer.transferId}
                      transfer={transfer}
                      direction="received"
                      acting={acting}
                      onAccept={(id) => void decide('accept', id)}
                      onReject={(id) => void decide('reject', id)}
                      onCancel={(id) => void decide('cancel', id)}
                    />
                  ))}
                </ul>
              )}
            </section>
            <section>
              <h4 className="text-xs font-semibold text-muted-foreground">发出的转交</h4>
              {sent.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">暂无发出的转交。</p>
              ) : (
                <ul className="mt-1 divide-y divide-border">
                  {sent.map((transfer) => (
                    <TransferItem
                      key={transfer.transferId}
                      transfer={transfer}
                      direction="sent"
                      acting={acting}
                      onAccept={(id) => void decide('accept', id)}
                      onReject={(id) => void decide('reject', id)}
                      onCancel={(id) => void decide('cancel', id)}
                    />
                  ))}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </Card>
  );
}

/** Bot 视角：当前 ownership + 管理者管理 + owner 发起转交。 */
function BotOwnershipPanel({
  botId,
  accessRelation,
}: {
  botId: string;
  accessRelation?: 'owner' | 'manager';
}) {
  const [ownership, setOwnership] = useState<BotOwnershipState | null>(null);
  const [managers, setManagers] = useState<BotManagerView | null>(null);
  const [pending, setPending] = useState<BotOwnershipTransferView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [recipient, setRecipient] = useState('');
  const [grantUserId, setGrantUserId] = useState('');
  const [busy, setBusy] = useState(false);
  const [teamSourceNotice, setTeamSourceNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [ownershipResult, managersResult, pendingResult] = await Promise.all([
      botAuthorityService.getOwnership(botId),
      botAuthorityService.listManagers(botId),
      botAuthorityService.listTransfers('sent', { status: 'pending', offset: 0, limit: 20 }),
    ]);
    let firstError: string | null = null;
    if (ownershipResult.ok) {
      setOwnership(ownershipResult.data);
    } else {
      firstError = ownershipResult.error.friendlyMessage;
    }
    if (managersResult.ok) {
      setManagers(managersResult.data);
    } else {
      firstError = firstError ?? managersResult.error.friendlyMessage;
    }
    if (firstError) setError(firstError);
    if (pendingResult.ok) {
      // 一个 Bot 只有一个 pending 槽位；这里只取当前 Bot 的那条。
      setPending(pendingResult.data.find((item) => item.botId === botId) ?? null);
    }
    setLoading(false);
  }, [botId]);

  useEffect(() => {
    void load();
  }, [load]);

  const reloadManagers = useCallback(async () => {
    const managersResult = await botAuthorityService.listManagers(botId);
    if (managersResult.ok) setManagers(managersResult.data);
  }, [botId]);

  const submitInitiate = useCallback(async () => {
    if (!ownership || busy) return;
    setBusy(true);
    const result = await botAuthorityService.createOwnershipTransfer(
      botId,
      recipient.trim(),
      ownership.ownershipVersion,
    );
    if (result.ok) {
      setPending(result.data);
      setFormOpen(false);
      setRecipient('');
      setInlineError(null);
    } else {
      setInlineError(result.error.friendlyMessage);
      if (result.error.code === 'OWNERSHIP_CHANGED') {
        // 版本快照过期：立即重读 ownership/管理面，再允许重试。
        await load();
      }
    }
    setBusy(false);
  }, [botId, busy, load, ownership, recipient]);

  const addManager = useCallback(async () => {
    const userId = grantUserId.trim();
    if (!userId || busy) return;
    setBusy(true);
    const result = await botAuthorityService.grantManager(botId, userId);
    if (result.ok) {
      setGrantUserId('');
      setInlineError(null);
      await reloadManagers();
    } else {
      setInlineError(result.error.friendlyMessage);
    }
    setBusy(false);
  }, [botId, busy, grantUserId, reloadManagers]);

  const removeManager = useCallback(
    async (userId: string) => {
      if (busy) return;
      setBusy(true);
      const result = await botAuthorityService.revokeManager(botId, userId);
      if (result.ok) {
        setInlineError(null);
        if (result.data.remaining_team_sources.length > 0) {
          // revoked=true ≠ 总失权：team/* 来源仍在，不由本地移除条目。
          setTeamSourceNotice(
            `${userId} 仍通过团队来源保留管理者权限（团队来源：${result.data.remaining_team_sources.join('、')}）`,
          );
        } else {
          setTeamSourceNotice(null);
          await reloadManagers();
        }
      } else {
        setInlineError(result.error.friendlyMessage);
      }
      setBusy(false);
    },
    [botId, busy, reloadManagers],
  );

  const cancelPending = useCallback(async () => {
    if (!pending || busy) return;
    setBusy(true);
    const result = await botAuthorityService.cancelTransfer(pending.transferId);
    if (result.ok) {
      setPending({ ...pending, status: result.data.status });
      setInlineError(null);
    } else {
      setInlineError(result.error.friendlyMessage);
    }
    setBusy(false);
  }, [busy, pending]);

  // owner 才能发起转交；已有 pending（slot 被占用）时同样不渲染发起入口。
  const canInitiate = accessRelation === 'owner' && !pending && !formOpen;

  if (loading) {
    return (
      <Card className="p-5">
        <h3 className="text-sm font-semibold text-foreground">BCS ownership 与管理者</h3>
        <p className="mt-3 text-sm text-muted-foreground">正在加载 ownership</p>
      </Card>
    );
  }

  return (
    <Card className="p-5">
      <h3 className="text-sm font-semibold text-foreground">BCS ownership 与管理者</h3>
      <div className="mt-2">
        <OwnershipScopeStatement />
      </div>
      {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
      {ownership && (
        <p className="mt-3 text-sm text-foreground">
          当前 Owner：{ownership.ownerUserId}
          <span className="ml-2 text-xs text-muted-foreground">
            ownership 版本：{ownership.ownershipVersion}
          </span>
        </p>
      )}

      <section className="mt-4">
        <h4 className="text-xs font-semibold text-muted-foreground">管理者</h4>
        {managers && managers.managers.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">暂无显式管理者。</p>
        ) : null}
        {managers && managers.managers.length > 0 && (
          <ul className="mt-1 divide-y divide-border">
            {managers.managers.map((manager) => (
              <li key={manager.userId} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="truncate">{manager.userId}</span>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy}
                  aria-label={`移除管理者 ${manager.userId}`}
                  onClick={() => void removeManager(manager.userId)}
                >
                  移除
                </Button>
              </li>
            ))}
          </ul>
        )}
        {teamSourceNotice && (
          <p className="mt-2 text-xs text-muted-foreground">{teamSourceNotice}</p>
        )}
        <div className="mt-3 flex items-center gap-2">
          <input
            aria-label="管理者 user_id"
            value={grantUserId}
            onChange={(event) => setGrantUserId(event.target.value)}
            className="h-8 w-56 rounded-lg border border-input bg-background px-2 text-sm"
            placeholder="user_id"
          />
          <Button size="sm" variant="secondary" loading={busy} onClick={() => void addManager()}>
            添加管理者
          </Button>
        </div>
      </section>

      {canInitiate && (
        <Button className="mt-4" onClick={() => setFormOpen(true)}>
          转交 ownership
        </Button>
      )}

      {formOpen && (
        <div className="mt-3 flex items-center gap-2">
          <input
            aria-label="收件人 user_id"
            value={recipient}
            onChange={(event) => setRecipient(event.target.value)}
            className="h-8 w-56 rounded-lg border border-input bg-background px-2 text-sm"
            placeholder="目标 Human user_id"
          />
          <Button size="sm" loading={busy} onClick={() => void submitInitiate()}>
            发起转交
          </Button>
        </div>
      )}

      {pending && (
        <div className="mt-3 flex items-center gap-3 rounded-lg bg-muted/40 px-3 py-2">
          <span className="text-sm text-foreground">
            转交待确认（收件人 {pending.toUserId}）
          </span>
          <TransferStatusBadge status={pending.status} />
          {accessRelation === 'owner' && pending.status === 'pending' && (
            <Button size="sm" variant="secondary" loading={busy} onClick={() => void cancelPending()}>
              取消转交
            </Button>
          )}
        </div>
      )}

      {inlineError && <p className="mt-3 text-sm text-destructive">{inlineError}</p>}
    </Card>
  );
}

/**
 * BCS ownership/manager 面板：
 * - Bot 视角：当前 ownership/manager 管理（owner 发起转交）；
 * - Human 视角：转交收发件（确认/拒绝/取消）。
 * 挂在协作权限页的 BCS 协作权限区域；只消费 botAuthorityService 的
 * ownership/version/pending，不自行推断当前 owner，不调用内部 team endpoints。
 */
export function BotOwnershipTransferPanel({
  mode,
  botId,
  accessRelation,
}: BotOwnershipTransferPanelProps) {
  if (mode === 'bot' && botId) {
    return <BotOwnershipPanel botId={botId} accessRelation={accessRelation} />;
  }
  if (mode === 'human') {
    return <HumanTransferInbox />;
  }
  return null;
}
