import type { BotCopyInput, BotDomain } from '@/services/botWorkshop';
import { botWorkshopService, BackendRequestError } from '@/services/botWorkshop';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { useBotCreateAuthorization } from './useBotCreateAuthorization';

interface BotWorkshopCopyFlowOptions {
  /** 复制成功 / 授权走通后,刷新列表以让新 Bot 出现。 */
  load: () => Promise<void>;
  /** 复制流程结束时(成功或终止)关闭源 Bot 引用。 */
  onClose: () => void;
}

/**
 * 编排 Bot 复制:调 `copyBot` →
 * - `issued`(success,无 AgentPass 授权):复制已提交,dst bot 由 create_job 异步创建,
 *   POST 200 时 detail 还查不到。**Modal 保持开启 + copying(busy 拦截关闭)**进入
 *   「复制后等待」:每 2s 轮询单 bot(dst 建好前 404,静默撤 toast 重试),dst 能查到才
 *   setCopying(false)+onClose()+load() 让新 Bot 落入当前页;60s 未出现则同兜底关窗 + 警告。
 * - `authorization_required`(auth_required,内源 frame url 走 AgentPass):**与创建 iframe
 *   url 完全同款** —— 复用 `useBotCreateAuthorization` 的 2s 轮询 dst bot 的 auth-status,
 *   「授权后等待」直到 ISSUED + bot 才结转 handleCopied → load()。
 *
 * 源 Bot 的协作者/外部渠道/工具授权/密钥不继承。
 */
export function useBotWorkshopCopyFlow({ load, onClose }: BotWorkshopCopyFlowOptions) {
  const [copying, setCopying] = useState(false);
  const handleCopied = useCallback(
    async (bot: BotDomain) => {
      onClose();
      toast.success(`${bot.name} 已复制`);
      await load();
    },
    [load, onClose],
  );
  const handleAuthorizationTerminated = useCallback(
    (status: string, message?: string) => {
      onClose();
      toast.error(message || `AgentPass 授权未完成(${status})`);
    },
    [onClose],
  );
  const authorization = useBotCreateAuthorization(handleCopied, handleAuthorizationTerminated);

  // issued(无需 AgentPass 授权)路径:POST 200 返回时 dst bot 由 create_job 异步创建,
  // detail 此刻查不到。submitCopy 保持 Modal 开 + copying=true(busy 拦截关闭),这里
  // 「复制后等待」:每 2s 轮询单 bot(dst 建好前 404,静默撤 toast 重试),dst 能查到才
  // setCopying(false)+onClose()+load();60s 未出现则同兜底关窗 + 警告 + 刷新一次。
  // (auth_required 路径的「授权后等待」由上方 useBotCreateAuthorization 轮询承担:
  //  poll 到 ISSUED + bot 才结转 handleCopied → load(),此处不复用。)
  const [pendingIssuedBotId, setPendingIssuedBotId] = useState<string>();
  useEffect(() => {
    if (!pendingIssuedBotId) return;
    let active = true;
    let terminal = false;
    const poll = async () => {
      if (terminal) return;
      // 单 bot 定向轮询(不受工作台 keyword/分页过滤影响)。dst 由 create_job 异步创建,
      // 建好前 detail 会 404 —— httpClient 已把该 404 enqueue 进 errorNotifyStore,这里
      // 按 toastKey 撤掉(观察者 drain 时跳过),不让它串成弹窗/扰乱页面,静默重试。
      let bot: BotDomain | undefined;
      try {
        bot = await botWorkshopService.detail(pendingIssuedBotId);
      } catch (error) {
        if (error instanceof BackendRequestError && error.status === 404 && error.toastKey) {
          useErrorNotifyStore.getState().cancel(error.toastKey);
        }
      }
      if (!active || terminal) return;
      if (bot) {
        terminal = true;
        setPendingIssuedBotId(undefined);
        setCopying(false);
        onClose();
        toast.success(`${bot.name} 已创建`);
        await load();
      }
    };
    void poll();
    const timer = window.setInterval(poll, 2000);
    const timeout = window.setTimeout(() => {
      if (active && !terminal) {
        terminal = true;
        setPendingIssuedBotId(undefined);
        setCopying(false);
        onClose();
        toast.warning('新 Bot 仍在创建中,请稍后刷新查看');
        void load();
      }
    }, 60_000);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.clearTimeout(timeout);
    };
  }, [pendingIssuedBotId, load, onClose]);

  const submitCopy = useCallback(
    async (input: BotCopyInput) => {
      setCopying(true);
      try {
        const result = await botWorkshopService.copyBot(input);
        if (result.type === 'authorization_required') {
          authorization.beginAuthorization(result);
          setCopying(false); // 授权面板自管 busy 状态
          return;
        }
        // issued:dst bot 由 create_job 异步创建,POST 200 时 detail 还查不到。
        // Modal 保持开 + copying=true(busy 拦截关闭)进入「复制后等待」轮询;
        // dst 出现 / 超时后由上方 pendingIssuedBotId effect 调 setCopying(false)+onClose()。
        toast.success('复制已提交,正在创建新 Bot');
        setPendingIssuedBotId(result.botId);
      } catch (error) {
        setCopying(false);
        toast.error(error instanceof Error ? error.message : 'Bot 复制失败');
        throw error;
      }
    },
    [authorization.beginAuthorization],
  );

  return {
    copying,
    authorization: authorization.authorization,
    cancelAuthorization: authorization.cancelAuthorization,
    submitCopy,
  };
}
