import { notifySuccess } from '@/components/ui/notify';
import type { FriendRequestActor, SquareResource } from '@/domain/collaborationSquare/types';
import {
  listPendingFriendConnectionRequests,
  mapFriendRequestWriteError,
  resolveFriendConnectionRequest,
  type FriendConnectionDirection,
  type FriendConnectionRequestRecord,
} from '@/services/collaborationSquare/friendConnectionRequestService';
import { getCollaborationSquareErrorMessage } from '@/utils/collaborationSquare';
import { safeReportError } from '@/utils/errorToast';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useCollaborationActorOperationScope, type CollaborationOperationGuard } from './useCollaborationActorOperationScope';

export type {
  FriendConnectionDirection,
  FriendConnectionRequestRecord,
} from '@/services/collaborationSquare/friendConnectionRequestService';

export interface FriendConnectionRequestsView {
  items: FriendConnectionRequestRecord[];
  loading: boolean;
  error: string | null;
  page: number;
  total: number;
  reload: () => void;
  gotoPage: (page: number) => void;
}

export interface FriendConnectionRequestsModel {
  sent: FriendConnectionRequestsView;
  received: FriendConnectionRequestsView;
  /** 正在提交的写操作 key（accept/reject/cancel + requestId）。 */
  busyRequestIds: string[];
  accept: (requestId: string) => void;
  reject: (requestId: string) => void;
  cancel: (requestId: string) => void;
}

export interface UseFriendConnectionRequestsOptions {
  resource: SquareResource;
  /** 当前所选操作角色（Human 只看本人；Bot 管理权限由 BCS 复验）。 */
  actor: FriendRequestActor | null;
  /** 登录用户 userId；与 actor 一同构成操作代次 scopeKey。 */
  userId?: string;
  /** 面板展开后才读取（收起／无角色不发请求）。 */
  enabled?: boolean;
  /** 审批／撤销成功后刷新广场卡片关系的回调（发起前核验同一 guard）。 */
  onRelationshipsChanged?: () => void;
}

type WriteAction = 'accept' | 'reject' | 'cancel';

interface DirectionViewState {
  items: FriendConnectionRequestRecord[];
  loading: boolean;
  error: string | null;
  page: number;
  total: number;
}

const EMPTY_VIEW: DirectionViewState = { items: [], loading: false, error: null, page: 1, total: 0 };

/** 写操作成功时应落到的申请终态；20000 但状态未变说明操作未生效。 */
const EXPECTED_WRITE_STATUS: Record<WriteAction, FriendConnectionRequestRecord['status']> = {
  accept: 'approved',
  reject: 'rejected',
  cancel: 'cancelled',
};

/**
 * 所选角色的「发出／收到」好友申请编排（bcs-standalone-friend-discovery Task 4）：
 * 读取复用现有 BCS friend-connections Controller（经 friendConnectionRequestService），角色
 * 切换用 AbortController 清理读取、并为当前角色重新加载；通过／拒绝／撤销是写操作——每次
 * await 后与 catch/finally 先核验代次，abort 只取消客户端等待、不代表服务端回滚，
 * 迟到的成功／失败均不得污染新角色的列表、busy、提示或导航。
 */
export function useFriendConnectionRequests({
  resource,
  actor,
  userId,
  enabled = true,
  onRelationshipsChanged,
}: UseFriendConnectionRequestsOptions): FriendConnectionRequestsModel {
  const actorId = actor?.id ?? '';
  const actorType = actor?.type ?? '';
  const operationScopeKey = JSON.stringify([userId ?? '', resource, actorType, actorId]);
  const captureOperation = useCollaborationActorOperationScope(operationScopeKey);

  const [views, setViews] = useState<Record<FriendConnectionDirection, DirectionViewState>>({
    sent: EMPTY_VIEW,
    received: EMPTY_VIEW,
  });
  const [busyRequestIds, setBusyRequestIds] = useState<string[]>([]);
  const requestSeqRef = useRef<Record<FriendConnectionDirection, number>>({ sent: 0, received: 0 });
  const loadControllerRef = useRef<AbortController | null>(null);
  // 本 Hook 当前代次持有的写操作 busy 归属：角色变化／卸载同步清理（ref 内记录），不等待旧请求 finally。
  const busyOwnersRef = useRef(new Map<string, CollaborationOperationGuard>());

  useLayoutEffect(() => {
    const owners = busyOwnersRef.current;
    return () => {
      for (const [key, isCurrent] of owners) {
        if (!isCurrent()) setBusyRequestIds((keys) => keys.filter((item) => item !== key));
      }
      owners.clear();
    };
  }, [operationScopeKey]);

  const setDirectionView = useCallback(
    (direction: FriendConnectionDirection, patch: Partial<DirectionViewState>) => {
      setViews((previous) => ({ ...previous, [direction]: { ...previous[direction], ...patch } }));
    },
    [],
  );

  const loadDirection = useCallback(
    async (
      direction: FriendConnectionDirection,
      actorAtLoad: FriendRequestActor,
      page: number,
      signal: AbortSignal | undefined,
    ) => {
      const isCurrent = captureOperation();
      const seq = (requestSeqRef.current[direction] += 1);
      setDirectionView(direction, { loading: true, error: null });
      try {
        const result = await listPendingFriendConnectionRequests(direction, actorAtLoad, page, signal);
        // 迟到读取（角色已切走／已翻页）不得覆盖当前列表：代次 + 序列双核验。
        if (!isCurrent() || seq !== requestSeqRef.current[direction]) return;
        setDirectionView(direction, { loading: false, error: null, items: result.items, page, total: result.total });
      } catch (error) {
        if (!isCurrent() || seq !== requestSeqRef.current[direction]) return;
        if ((error as Error).name === 'AbortError') {
          // 当前代次被 AbortSignal 取消也要收敛 loading，否则面板会一直停在加载态。
          setDirectionView(direction, { loading: false });
          return;
        }
        setDirectionView(direction, { loading: false, error: getCollaborationSquareErrorMessage(error) });
      }
    },
    [captureOperation, setDirectionView],
  );

  useEffect(() => {
    if (!enabled || !actor) return;
    // 切换角色或重新展开：不复用上一角色请求与列表展示，按当前角色重新加载。
    setViews({ sent: EMPTY_VIEW, received: EMPTY_VIEW });
    requestSeqRef.current = { sent: 0, received: 0 };
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    void loadDirection('received', actor, 1, controller.signal);
    void loadDirection('sent', actor, 1, controller.signal);
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [actorId, actorType, userId, enabled, loadDirection]);

  const runRequestWrite = useCallback(
    async (action: WriteAction, requestId: string, successMessage: string) => {
      if (!actor) return;
      const busyKey = `${action}:${requestId}`;
      if (busyRequestIds.includes(busyKey)) return;
      const isCurrent = captureOperation();
      const writeActor = actor;
      // 失败同样以服务端为准刷新两向列表：并发审批／对方撤回后服务端已变更，本地行不能永久停留「待处理」。
      const reloadDirections = () => {
        const signal = loadControllerRef.current?.signal;
        void loadDirection('received', writeActor, 1, signal);
        void loadDirection('sent', writeActor, 1, signal);
      };
      setBusyRequestIds((keys) => [...new Set([...keys, busyKey])]);
      busyOwnersRef.current.set(busyKey, isCurrent);
      try {
        // 写操作不因角色切换取消（abort 不是服务端回滚）：独立 signal 只透传取消形状。
        const controller = new AbortController();
        const record = await resolveFriendConnectionRequest(action, requestId, controller.signal);
        if (!isCurrent()) return;
        if (record.status !== EXPECTED_WRITE_STATUS[action]) {
          // 20000 但记录未落到对应终态：不报假成功，刷新后由服务端状态决定展示。
          safeReportError(new Error('操作未生效，列表已刷新'));
          reloadDirections();
          return;
        }
        notifySuccess(successMessage);
        // 发起卡片关系刷新与申请列表刷新前核验同一 guard；读取结果的应用由 loadDirection
        // 的代次/序列守卫复验。
        if (!isCurrent()) return;
        onRelationshipsChanged?.();
        reloadDirections();
      } catch (error) {
        if (!isCurrent()) return;
        safeReportError(mapFriendRequestWriteError(error));
        reloadDirections();
      } finally {
        if (isCurrent()) {
          busyOwnersRef.current.delete(busyKey);
          setBusyRequestIds((keys) => keys.filter((item) => item !== busyKey));
        }
      }
    },
    [actor, busyRequestIds, captureOperation, loadDirection, onRelationshipsChanged],
  );

  const makeView = useCallback(
    (direction: FriendConnectionDirection): FriendConnectionRequestsView => ({
      items: views[direction].items,
      loading: views[direction].loading,
      error: views[direction].error,
      page: views[direction].page,
      total: views[direction].total,
      reload: () => {
        if (actor) void loadDirection(direction, actor, views[direction].page, loadControllerRef.current?.signal);
      },
      gotoPage: (page: number) => {
        if (actor) void loadDirection(direction, actor, page, loadControllerRef.current?.signal);
      },
    }),
    [actor, loadDirection, views],
  );

  return {
    sent: makeView('sent'),
    received: makeView('received'),
    busyRequestIds,
    accept: (requestId: string) => {
      void runRequestWrite('accept', requestId, '已通过好友申请');
    },
    reject: (requestId: string) => {
      void runRequestWrite('reject', requestId, '已拒绝好友申请');
    },
    cancel: (requestId: string) => {
      void runRequestWrite('cancel', requestId, '已撤销好友申请');
    },
  };
}
