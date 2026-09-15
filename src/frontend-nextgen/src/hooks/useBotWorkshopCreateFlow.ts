import type { BotCreateInput, BotDomain } from '@/services/botWorkshop';
import { botWorkshopService } from '@/services/botWorkshop';
import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { useBotCreateAuthorization } from './useBotCreateAuthorization';

interface BotWorkshopCreateFlowOptions {
  load: () => Promise<void>;
  setCreateScenario: (scenario: 'cloud' | 'local' | undefined) => void;
}

/** 编排创建、AgentPass 授权及成功后列表刷新，避免列表 Hook 承担流程细节。 */
export function useBotWorkshopCreateFlow({ load, setCreateScenario }: BotWorkshopCreateFlowOptions) {
  const [creating, setCreating] = useState(false);
  const handleCreated = useCallback(
    async (bot: BotDomain) => {
      setCreateScenario(undefined);
      toast.success(`${bot.name} 已创建`);
      await load();
    },
    [load, setCreateScenario],
  );
  const handleAuthorizationTerminated = useCallback(
    (status: string, message?: string) => {
      setCreateScenario(undefined);
      toast.error(message || `AgentPass 授权未完成（${status}）`);
    },
    [setCreateScenario],
  );
  const authorization = useBotCreateAuthorization(handleCreated, handleAuthorizationTerminated);
  const submitCreate = useCallback(
    async (input: BotCreateInput) => {
      setCreating(true);
      try {
        const result = await botWorkshopService.create(input);
        if (result.type === 'authorization_required') {
          authorization.beginAuthorization(result);
          return;
        }
        if (result.type === 'created_with_pending_after_create') {
          const actions = result.afterCreateFailures.map((failure) => failure.key).join('、');
          toast.warning(`Bot 已创建，但后续配置未全部完成${actions ? `：${actions}` : ''}`);
        }
        await handleCreated(result.bot);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Bot 创建失败');
        throw error;
      } finally {
        setCreating(false);
      }
    },
    [authorization.beginAuthorization, handleCreated],
  );

  return {
    creating,
    authorization: authorization.authorization,
    cancelAuthorization: authorization.cancelAuthorization,
    submitCreate,
  };
}
