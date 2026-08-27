import type { BotDomain } from '@/services/botWorkshop';
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

  return {
    access,
    collaborators,
    closeAccess: () => setAccess({ spaces: [], loading: false }),
    openSpaceChange: (bot: BotDomain) => void openAccess('space', bot),
    collaborationModeFor: (bot: BotDomain) => (isOwner(bot) ? ('authorize' as const) : ('request' as const)),
    openAuthorize: (bot: BotDomain) => void openAccess(isOwner(bot) ? 'authorize' : 'request', bot),
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
    addCollaborator: async (userId: string, role: BotCollaborator['role']) => {
      if (!access.bot) return;
      await botManagementService.addCollaborator(access.bot.id, userId, role);
      setCollaborators(await botManagementService.listCollaborators(access.bot.id));
      toast.success('已添加协作者');
    },
    updateCollaborator: async (id: number, role: BotCollaborator['role']) => {
      if (!access.bot) return;
      await botManagementService.updateCollaborator(access.bot.id, id, role);
      setCollaborators(await botManagementService.listCollaborators(access.bot.id));
    },
    removeCollaborator: async (id: number) => {
      if (!access.bot) return;
      await botManagementService.removeCollaborator(access.bot.id, id);
      setCollaborators((items) => items.filter((item) => item.id !== id));
      toast.success('已移除协作者');
    },
    requestAccess: async (reason: string) => {
      if (!access.bot) return;
      await botManagementService.requestAccess(access.bot, reason);
      toast.success('操作权限申请已提交');
      setAccess({ spaces: [], loading: false });
    },
  };
}
