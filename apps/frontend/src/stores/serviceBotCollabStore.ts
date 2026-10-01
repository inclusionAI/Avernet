/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Service Bot Collaboration Store
 * 服务 Bot 的协作者、协作锁、沙箱只读规则三合一状态管理
 *
 * 接口文档：内部接口文档
 */

import * as BotController from '@/services/backend-api/BotController';
import type {
  CollaboratorInfo,
  CollaboratorRole,
  LockInfo,
  ReadOnlyRule,
  ReadOnlyTreeNode,
} from '@/services/backend-api/ServiceBotController';
import * as ServiceBotController from '@/services/backend-api/ServiceBotController';
import { USE_SERVICE_BOT_COLLAB_MOCK as USE_MOCK } from '@/services/mock';
import * as ServiceBotMock from '@/services/mock/ServiceBotController';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

/** 协作 Mock 开关：集中在 src/services/mock/index.ts，禁止在本文件重新定义 */

/** 只读规则目录树某一层的快照（按 path 维度懒加载） */
export interface ReadOnlyTreeSlice {
  base_path: string;
  items: ReadOnlyTreeNode[];
  default_rules: ReadOnlyRule[];
  custom_rules: ReadOnlyRule[];
}

export interface ServiceBotCollabState {
  /** 当前 Bot + Owner（同 botId 不同 owner 视为不同协作上下文） */
  currentBotId: string | null;
  currentOwnerId: string | null;

  collaborators: CollaboratorInfo[];
  /** 锁信息（null 表示未锁定） */
  lockInfo: LockInfo | null;
  /** 后端计算的「是否需要锁才能编辑」标志（无协作者时 false，owner 独占） */
  needLock: boolean;
  /** 自定义只读规则（来自 tree 接口的 custom_rules，本地编辑直接更新） */
  customReadOnlyRules: ReadOnlyRule[];
  /** 系统默认只读规则（只展示，不可编辑） */
  defaultReadOnlyRules: ReadOnlyRule[];
  /** 只读规则查询根路径（从 tree 接口返回） */
  readOnlyBasePath: string;
  /** 当前已加载的目录层数据，key = 相对 path（空字符串为根） */
  readOnlyTreeByPath: Record<string, ReadOnlyTreeSlice>;

  // 加载态
  isLoading: boolean;
  isAddingCollaborator: boolean;
  isRemovingCollaborator: number | null; // 当前正在删除的协作者 id
  isUpdatingCollaborator: number | null;
  isSavingReadOnlyRules: boolean;
  isAcquiringLock: boolean;
  isReleasingLock: boolean;
  isStealingLock: boolean;
  isLoadingTreePath: string | null; // 当前正在加载的相对 path
  isLoadingCustomRules: boolean; // 接口 3 (GET /api/bots/{bot_id}) 加载态

  // Actions
  loadCollabData: (botId: string, ownerId: string) => Promise<void>;
  refreshLockInfo: (botId: string, ownerId: string) => Promise<LockInfo | null>;

  addCollaborator: (params: {
    botId: string;
    ownerId: string;
    userId: string;
    userName?: string;
    role?: CollaboratorRole;
  }) => Promise<CollaboratorInfo | null>;
  removeCollaborator: (id: number) => Promise<boolean>;
  updateCollaboratorRole: (
    id: number,
    role: CollaboratorRole,
  ) => Promise<boolean>;

  acquireLock: (botId: string, ownerId: string) => Promise<boolean>;
  releaseLock: (
    botId: string,
    ownerId: string,
    force?: boolean,
  ) => Promise<boolean>;
  stealLock: (botId: string, ownerId: string) => Promise<boolean>;

  loadReadOnlyTree: (
    botId: string,
    relativePath: string,
    recursive?: boolean,
  ) => Promise<void>;
  saveReadOnlyRules: (botId: string, rules: ReadOnlyRule[]) => Promise<boolean>;
  toggleCustomReadOnly: (
    botId: string,
    rule: ReadOnlyRule,
    enable: boolean,
  ) => Promise<boolean>;
  /** 从 bot 详情接口 (GET /api/bots/{bot_id}) 拉取 ext.read_only_rules 写入 customReadOnlyRules */
  loadCustomRulesFromBotDetail: (
    botId: string,
    ownerId: string,
  ) => Promise<void>;

  reset: () => void;
}

const initialState = {
  currentBotId: null,
  currentOwnerId: null,
  collaborators: [],
  lockInfo: null,
  needLock: true,
  customReadOnlyRules: [],
  defaultReadOnlyRules: [],
  readOnlyBasePath: '',
  readOnlyTreeByPath: {},
  isLoading: false,
  isAddingCollaborator: false,
  isRemovingCollaborator: null,
  isUpdatingCollaborator: null,
  isSavingReadOnlyRules: false,
  isAcquiringLock: false,
  isReleasingLock: false,
  isStealingLock: false,
  isLoadingTreePath: null,
  isLoadingCustomRules: false,
};

export const useServiceBotCollabStore = create<ServiceBotCollabState>()(
  devtools(
    (set, get) => ({
      ...initialState,

      loadCollabData: async (botId, ownerId) => {
        const isSameContext =
          get().currentBotId === botId && get().currentOwnerId === ownerId;
        if (!isSameContext) {
          set(
            {
              ...initialState,
              currentBotId: botId,
              currentOwnerId: ownerId,
              isLoading: true,
            },
            false,
            'loadCollabData/start',
          );
        } else {
          set({ isLoading: true }, false, 'loadCollabData/start');
        }

        try {
          const [listRes, lockRes] = await Promise.all([
            USE_MOCK
              ? ServiceBotMock.listCollaboratorsMock({
                  bot_id: botId,
                  owner_id: ownerId,
                })
              : ServiceBotController.listCollaborators({
                  bot_id: botId,
                  owner_id: ownerId,
                }),
            USE_MOCK
              ? ServiceBotMock.getLockInfoMock(botId, ownerId)
              : ServiceBotController.getLockInfo({
                  bot_id: botId,
                  owner_id: ownerId,
                }),
          ]);

          // 竞态保护
          if (
            get().currentBotId !== botId ||
            get().currentOwnerId !== ownerId
          ) {
            return;
          }

          set(
            {
              collaborators:
                (listRes.success && listRes.data?.collaborators) || [],
              lockInfo: lockRes.success ? lockRes.data?.lock ?? null : null,
              needLock: lockRes.success
                ? lockRes.data?.need_lock ?? true
                : true,
              isLoading: false,
            },
            false,
            'loadCollabData/done',
          );
        } catch (err) {
          console.error('[serviceBotCollabStore] loadCollabData error:', err);
          if (
            get().currentBotId === botId &&
            get().currentOwnerId === ownerId
          ) {
            set({ isLoading: false }, false, 'loadCollabData/error');
          }
          throw err;
        }
      },

      refreshLockInfo: async (botId, ownerId) => {
        try {
          const res = USE_MOCK
            ? await ServiceBotMock.getLockInfoMock(botId, ownerId)
            : await ServiceBotController.getLockInfo({
                bot_id: botId,
                owner_id: ownerId,
              });
          const next = res.success ? res.data?.lock ?? null : null;
          if (
            get().currentBotId === botId &&
            get().currentOwnerId === ownerId
          ) {
            set(
              {
                lockInfo: next,
                needLock: res.success ? res.data?.need_lock ?? true : true,
              },
              false,
              'refreshLockInfo',
            );
          }
          return next;
        } catch (err) {
          console.error('[serviceBotCollabStore] refreshLockInfo error:', err);
          return null;
        }
      },

      addCollaborator: async ({
        botId,
        ownerId,
        userId,
        userName,
        role = 'admin',
      }) => {
        set({ isAddingCollaborator: true }, false, 'addCollaborator/start');
        try {
          const res = USE_MOCK
            ? await ServiceBotMock.addCollaboratorMock({
                bot_id: botId,
                owner_id: ownerId,
                user_id: userId,
                user_name: userName,
                role,
              })
            : await ServiceBotController.addCollaborator({
                bot_id: botId,
                owner_id: ownerId,
                user_id: userId,
                user_name: userName,
                role,
              });

          if (res.success && res.data) {
            set(
              (state) => ({
                collaborators: [...state.collaborators, res.data!],
                isAddingCollaborator: false,
              }),
              false,
              'addCollaborator/done',
            );
            return res.data;
          }
          set({ isAddingCollaborator: false }, false, 'addCollaborator/fail');
          return null;
        } catch (err) {
          console.error('[serviceBotCollabStore] addCollaborator error:', err);
          set({ isAddingCollaborator: false }, false, 'addCollaborator/error');
          throw err;
        }
      },

      removeCollaborator: async (id) => {
        set({ isRemovingCollaborator: id }, false, 'removeCollaborator/start');
        try {
          const res = USE_MOCK
            ? await ServiceBotMock.removeCollaboratorMock(id)
            : await ServiceBotController.removeCollaborator({ id });
          if (res.success) {
            set(
              (state) => ({
                collaborators: state.collaborators.filter((c) => c.id !== id),
                isRemovingCollaborator: null,
              }),
              false,
              'removeCollaborator/done',
            );
            return true;
          }
          set(
            { isRemovingCollaborator: null },
            false,
            'removeCollaborator/fail',
          );
          return false;
        } catch (err) {
          console.error(
            '[serviceBotCollabStore] removeCollaborator error:',
            err,
          );
          set(
            { isRemovingCollaborator: null },
            false,
            'removeCollaborator/error',
          );
          throw err;
        }
      },

      updateCollaboratorRole: async (id, role) => {
        set(
          { isUpdatingCollaborator: id },
          false,
          'updateCollaboratorRole/start',
        );
        try {
          const res = USE_MOCK
            ? await ServiceBotMock.updateCollaboratorMock({ id, role })
            : await ServiceBotController.updateCollaborator({ id, role });
          if (res.success && res.data) {
            set(
              (state) => ({
                collaborators: state.collaborators.map((c) =>
                  c.id === id ? res.data! : c,
                ),
                isUpdatingCollaborator: null,
              }),
              false,
              'updateCollaboratorRole/done',
            );
            return true;
          }
          set(
            { isUpdatingCollaborator: null },
            false,
            'updateCollaboratorRole/fail',
          );
          return false;
        } catch (err) {
          console.error(
            '[serviceBotCollabStore] updateCollaboratorRole error:',
            err,
          );
          set(
            { isUpdatingCollaborator: null },
            false,
            'updateCollaboratorRole/error',
          );
          throw err;
        }
      },

      acquireLock: async (botId, ownerId) => {
        set({ isAcquiringLock: true }, false, 'acquireLock/start');
        try {
          const res = USE_MOCK
            ? await ServiceBotMock.acquireLockMock(botId, ownerId)
            : await ServiceBotController.acquireLock({
                bot_id: botId,
                owner_id: ownerId,
              });

          // 锁被他人持有时仍是 success=true，但 acquired=false
          const acquired = !!res.data?.acquired;
          let lock = res.data?.lock ?? null;

          // 后端契约：抢锁失败时 data.lock = null（见接口文档）。
          // 主动补查持锁人，否则下游 UI/toast 拿不到 holder_user_id。
          if (!acquired && !lock) {
            try {
              const infoRes = USE_MOCK
                ? await ServiceBotMock.getLockInfoMock(botId, ownerId)
                : await ServiceBotController.getLockInfo({
                    bot_id: botId,
                    owner_id: ownerId,
                  });
              lock = infoRes.data?.lock ?? null;
            } catch (e) {
              console.warn(
                '[serviceBotCollabStore] acquireLock 失败后补查 getLockInfo 失败',
                e,
              );
            }
          }

          // 不论成功失败都回写 lock（失败时 lock 含持锁人；补查仍失败则保持 null）
          set(
            {
              lockInfo: lock,
              isAcquiringLock: false,
            },
            false,
            acquired ? 'acquireLock/done' : 'acquireLock/conflict',
          );
          return acquired;
        } catch (err) {
          console.error('[serviceBotCollabStore] acquireLock error:', err);
          set({ isAcquiringLock: false }, false, 'acquireLock/error');
          throw err;
        }
      },

      releaseLock: async (botId, ownerId, force = false) => {
        set({ isReleasingLock: true }, false, 'releaseLock/start');
        try {
          const res = USE_MOCK
            ? await ServiceBotMock.releaseLockMock(botId, ownerId, force)
            : await ServiceBotController.releaseLock({
                bot_id: botId,
                owner_id: ownerId,
                force,
              });
          if (res.success) {
            set(
              { lockInfo: null, isReleasingLock: false },
              false,
              'releaseLock/done',
            );
            return true;
          }
          set({ isReleasingLock: false }, false, 'releaseLock/fail');
          return false;
        } catch (err) {
          console.error('[serviceBotCollabStore] releaseLock error:', err);
          set({ isReleasingLock: false }, false, 'releaseLock/error');
          throw err;
        }
      },

      stealLock: async (botId, ownerId) => {
        set({ isStealingLock: true }, false, 'stealLock/start');
        try {
          const res = USE_MOCK
            ? await ServiceBotMock.stealLockMock(botId, ownerId)
            : await ServiceBotController.stealLock({
                bot_id: botId,
                owner_id: ownerId,
              });
          const stolen = res.success && !!res.data?.stolen;
          set(
            {
              lockInfo: stolen ? res.data?.lock ?? null : get().lockInfo,
              isStealingLock: false,
            },
            false,
            stolen ? 'stealLock/done' : 'stealLock/fail',
          );
          return stolen;
        } catch (err) {
          console.error('[serviceBotCollabStore] stealLock error:', err);
          set({ isStealingLock: false }, false, 'stealLock/error');
          throw err;
        }
      },

      loadReadOnlyTree: async (botId, relativePath, recursive = false) => {
        set(
          { isLoadingTreePath: relativePath },
          false,
          'loadReadOnlyTree/start',
        );
        try {
          // 注入 owner_id 让协作场景下后端按 bot 归属鉴权
          const ownerIdForApi = get().currentOwnerId ?? undefined;
          const res = USE_MOCK
            ? await ServiceBotMock.getReadOnlyTreeMock(botId, relativePath)
            : await ServiceBotController.getReadOnlyTree({
                bot_id: botId,
                owner_id: ownerIdForApi,
                path: relativePath,
                recursive,
              });

          if (res.success && res.data) {
            const slice: ReadOnlyTreeSlice = {
              base_path: res.data.base_path,
              items: res.data.items,
              default_rules: res.data.default_rules,
              custom_rules: res.data.custom_rules,
            };
            set(
              (state) => ({
                readOnlyBasePath: res.data!.base_path,
                defaultReadOnlyRules: res.data!.default_rules,
                // customReadOnlyRules 不再由 tree 接口写入，改由 loadCustomRulesFromBotDetail（接口 3）统一管理
                readOnlyTreeByPath: {
                  ...state.readOnlyTreeByPath,
                  [relativePath]: slice,
                },
                isLoadingTreePath: null,
              }),
              false,
              'loadReadOnlyTree/done',
            );
          } else {
            set({ isLoadingTreePath: null }, false, 'loadReadOnlyTree/fail');
          }
        } catch (err) {
          console.error('[serviceBotCollabStore] loadReadOnlyTree error:', err);
          set({ isLoadingTreePath: null }, false, 'loadReadOnlyTree/error');
          throw err;
        }
      },

      saveReadOnlyRules: async (botId, rules) => {
        set({ isSavingReadOnlyRules: true }, false, 'saveReadOnlyRules/start');
        try {
          // 注入 owner_id（来自 store 上下文）让协作场景下后端能按 bot 归属鉴权；
          // owner 自己操作时 currentOwnerId 也是自己，传也无副作用
          const ownerIdForApi = get().currentOwnerId ?? undefined;
          const res = USE_MOCK
            ? await ServiceBotMock.saveReadOnlyRulesMock(botId, rules)
            : await ServiceBotController.saveReadOnlyRules({
                bot_id: botId,
                owner_id: ownerIdForApi,
                rules,
              });
          if (res?.success) {
            set(
              {
                customReadOnlyRules: rules,
                isSavingReadOnlyRules: false,
              },
              false,
              'saveReadOnlyRules/done',
            );
            return true;
          }
          set(
            { isSavingReadOnlyRules: false },
            false,
            'saveReadOnlyRules/fail',
          );
          return false;
        } catch (err) {
          console.error(
            '[serviceBotCollabStore] saveReadOnlyRules error:',
            err,
          );
          set(
            { isSavingReadOnlyRules: false },
            false,
            'saveReadOnlyRules/error',
          );
          throw err;
        }
      },

      toggleCustomReadOnly: async (botId, rule, enable) => {
        const cur = get().customReadOnlyRules;
        const next = enable
          ? cur.some((r) => r.path === rule.path)
            ? cur
            : [...cur, rule]
          : cur.filter((r) => r.path !== rule.path);
        return get().saveReadOnlyRules(botId, next);
      },

      loadCustomRulesFromBotDetail: async (botId, ownerId) => {
        set({ isLoadingCustomRules: true }, false, 'loadCustomRules/start');
        try {
          const res = await BotController.getBotDetail({
            bot_id: botId,
            owner_id: ownerId,
          });

          // 竞态保护：切换 botId/ownerId 后旧结果不覆盖
          if (
            get().currentBotId !== botId ||
            get().currentOwnerId !== ownerId
          ) {
            return;
          }

          if (res.success) {
            // GET /api/bots/{bot_id} 的 data 直接是 Bot 对象（不是 {bot: Bot} 包装）。
            // CreateBotResponse 类型是给「创建」接口复用的（有 auth/success 两种 data），
            // 此处的 in 守卫对真实响应不成立——返回的 data 包含 bot_id/ext 等扁平字段，
            // 写成 data.bot 会一直拿到 undefined，导致自定义只读规则永远回显空。
            const data = res.data as any;
            const rules: ReadOnlyRule[] = data?.ext?.read_only_rules ?? [];
            set(
              { customReadOnlyRules: rules, isLoadingCustomRules: false },
              false,
              'loadCustomRules/done',
            );
          } else {
            set(
              { customReadOnlyRules: [], isLoadingCustomRules: false },
              false,
              'loadCustomRules/fail',
            );
          }
        } catch (err) {
          console.error(
            '[serviceBotCollabStore] loadCustomRulesFromBotDetail error:',
            err,
          );
          if (
            get().currentBotId === botId &&
            get().currentOwnerId === ownerId
          ) {
            set(
              { customReadOnlyRules: [], isLoadingCustomRules: false },
              false,
              'loadCustomRules/error',
            );
          }
          throw err;
        }
      },

      reset: () => set(initialState, false, 'reset'),
    }),
    { name: 'ServiceBotCollabStore' },
  ),
);
