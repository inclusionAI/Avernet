import type { BotDomain } from '@/services/botWorkshop';
import { getBotCollaborationMode } from '@/services/botWorkshop';
import {
  botManagementService,
  type BotCollaborator,
  type BotSpaceOption,
} from '@/services/botWorkshop/botManagementService';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';

interface AccessState {
  mode?: 'space' | 'authorize' | 'request';
  bot?: BotDomain;
  spaces: BotSpaceOption[];
  loading: boolean;
  operation?: string;
}

export function useBotWorkshopAccess(currentUserId: string | undefined, reload: () => Promise<void>) {
  const [access, setAccess] = useState<AccessState>({ spaces: [], loading: false });
  const [collaborators, setCollaborators] = useState<BotCollaborator[]>([]);
  const openAccess = useCallback(
    async (mode: AccessState['mode'], bot: BotDomain) => {
      if (!mode) return;
      setAccess({ mode, bot, spaces: [], loading: mode !== 'request' });
      try {
        if (mode === 'space' && currentUserId) {
          const spaces = await botManagementService.listSpaces(currentUserId);
          setAccess({ mode, bot, spaces, loading: false });
        } else if (mode === 'authorize') {
          setCollaborators(await botManagementService.listCollaborators(bot.id));
          setAccess({ mode, bot, spaces: [], loading: false });
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '权限信息加载失败');
        setAccess({ mode, bot, spaces: [], loading: false });
      }
    },
    [currentUserId],
  );
  const isOwner = (bot: BotDomain) => Boolean(currentUserId && bot.ownerId === currentUserId);
  const collaborationModeFor = (bot: BotDomain) => {
    return getBotCollaborationMode(bot, isOwner(bot));
  };

  return {
    access,
    collaborators,
    closeAccess: () => setAccess({ spaces: [], loading: false }),
    canChangeSpace: isOwner,
    openSpaceChange: (bot: BotDomain) => void openAccess('space', bot),
    collaborationModeFor,
    openAuthorize: (bot: BotDomain) => {
      const mode = collaborationModeFor(bot);
      if (mode) void openAccess(mode, bot);
    },
    changeSpace: async (spaceId: number) => {
      if (!access.bot || !currentUserId) {
        toast.error('缺少当前用户身份');
        return;
      }
      setAccess((value) => ({ ...value, loading: true }));
      try {
        await botManagementService.changeSpace(access.bot, spaceId, currentUserId);
        toast.success('归属空间已变更');
        setAccess({ spaces: [], loading: false });
        await reload();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '变更空间失败');
        setAccess((value) => ({ ...value, loading: false }));
      }
    },
    createTeamAndChangeSpace: async (name: string) => {
      if (!access.bot || !currentUserId) {
        toast.error('缺少当前用户身份');
        return;
      }
      setAccess((value) => ({ ...value, loading: true }));
      try {
        const space = await botManagementService.createTeamSpace(name, currentUserId);
        await botManagementService.changeSpace(access.bot, space.id, currentUserId);
        toast.success(`已创建「${space.name}」并变更 Bot 归属`);
        setAccess({ spaces: [], loading: false });
        await reload();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '创建团队并变更空间失败');
        setAccess((value) => ({ ...value, loading: false }));
      }
    },
    addCollaborator: async (userId: string, name: string | undefined, role: BotCollaborator['role']) => {
      if (!access.bot) return false;
      setAccess((value) => ({ ...value, operation: 'add' }));
      try {
        const added = await botManagementService.addCollaborator(access.bot.id, userId, name, role);
        setCollaborators((items) => [...items, added]);
        toast.success('已添加协作者');
        return true;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '添加协作者失败');
        return false;
      } finally {
        setAccess((value) => ({ ...value, operation: undefined }));
      }
    },
    updateCollaborator: async (id: number, role: BotCollaborator['role']) => {
      if (!access.bot) return;
      setAccess((value) => ({ ...value, operation: `update:${id}` }));
      try {
        const updated = await botManagementService.updateCollaborator(access.bot.id, id, role);
        setCollaborators((items) => items.map((item) => (item.id === id ? updated : item)));
        toast.success('成员角色已更新');
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '成员角色更新失败');
      } finally {
        setAccess((value) => ({ ...value, operation: undefined }));
      }
    },
    removeCollaborator: async (id: number) => {
      if (!access.bot) return;
      setAccess((value) => ({ ...value, operation: `remove:${id}` }));
      try {
        await botManagementService.removeCollaborator(access.bot.id, id);
        setCollaborators((items) => items.filter((item) => item.id !== id));
        toast.success('已移除协作者');
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '移除协作者失败');
      } finally {
        setAccess((value) => ({ ...value, operation: undefined }));
      }
    },
    requestAccess: async (reason: string) => {
      if (!access.bot) return;
      await botManagementService.requestAccess(access.bot, reason);
      toast.success('操作权限申请已提交');
      setAccess({ spaces: [], loading: false });
    },
  };
}
