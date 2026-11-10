import type { BotCopyInput, BotCreateAuthorization, BotDomain } from '@/services/botWorkshop';
import { botWorkshopService, BackendRequestError } from '@/services/botWorkshop';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

interface BotWorkshopCopyFlowOptions {
  /** 复制成功 / 授权走通后,刷新列表以让新 Bot 出现。 */
  load: () => Promise<void>;
  /** 复制流程结束时(成功或终止)关闭源 Bot 引用。 */
  onClose: () => void;
}

/**
 * 编排 Bot 复制:调 `copyBot` → 两条路径共用同一个 detail-wait 闸关窗。
 * - `issued`(success,无 AgentPass 授权):复制已提交,dst bot 由 create_job 异步创建,
 *   POST 200 时 detail 还查不到。**Modal 保持开启 + copying(busy 拦截关闭)**进入
 *   「复制后等待」:每 2s 轮询单 bot(dst 建好前 404,静默撤 toast 重试),dst 能查到才
 *   setCopying(false)+onClose()+load() 让新 Bot 落入当前页;60s 未出现则同兜底关窗 + 警告。
 * - `authorization_required`(auth_required,内源 iframe url 走 AgentPass):**不走**
 *   `useBotCreateAuthorization` 的 auth-status 轮询(copy dst 无 auth-status,轮询它无意义)。
 *   AuthorizationPanel 只承载 iframe 授权页。**detail-wait 不在 submit 起** —— dst bot_id 暂存
 *   ref,等 AgentPass 授权完成(iframe 内 Apply/License app postMessage `AGENT_LICENSE_CLOSE`)
 *   才灌进 pendingIssuedBotId 起 detail-wait,避免 auth 前那段 404 轮询。CLOSE 时清掉
 *   authorization 卸 iframe、维持 copying=true,让 CopyBotModal 的「复制中」罩(awaitingDetail)
 *   接管持住;detail 可查才 setCopying(false)+onClose()+toast+load()。
 *
 * 源 Bot 的协作者/外部渠道/工具授权/密钥不继承。
 */
export function useBotWorkshopCopyFlow({ load, onClose }: BotWorkshopCopyFlowOptions) {
  const [copying, setCopying] = useState(false);
  // auth_required 路径的 iframe 授权视图(只展示,不轮询 auth-status)。
  const [authorization, setAuthorization] = useState<
    (BotCreateAuthorization & { message?: string; error?: string }) | undefined
  >();

  // 「复制后等待」闸:poll detail(dst_bot_id) 直到可查。issued 与 auth_required 两路径共用。
  // dst 由 create_job 异步创建,建好前 detail 会 404 —— httpClient 已把该 404 enqueue 进
  // errorNotifyStore,这里按 toastKey 撤掉(观察者 drain 时跳过),不让它串成弹窗/扰乱页面,静默重试。
  // 可查 → setAuthorization(undefined)+setCopying(false)+onClose()+toast+load();60s 未出现则同兜底。
  const [pendingIssuedBotId, setPendingIssuedBotId] = useState<string>();
  // auth_required 路径:dst bot_id 暂存于此。submit 时不立刻起 detail-wait(避免 auth 前 404 轮询),
  // 等 AgentPass 授权完成(AGENT_LICENSE_CLOSE)再把 ref 灌进 pendingIssuedBotId 起 detail-wait。
  const pendingAuthBotIdRef = useRef<string>();

  // AgentPass 授权页 postMessage 桥(主壳不替 nextgen 清 authorization,nextgen 自己接):
  //   AGENT_LICENSE_CONFIRM = 授权提交成功(auth-done 信号,不改变状态)
  //   AGENT_LICENSE_CLOSE   = 授权浮窗要关(倒计时关 / 用户关)= auth 完成。此时才起 detail-wait:
  //                           把暂存的 dst bot_id 灌进 pendingIssuedBotId、清掉 authorization 卸 iframe、
  //                           维持 copying=true,让 CopyBotModal「复制中」罩(awaitingDetail)接管持住,
  //                           detail 可查才 setCopying(false)+onClose()+toast+load()。
  // 仅在 auth 阶段(authorization 在)监听 —— CLOSE 只会在这段来;auth 前/后都不监听,避免串扰。
  useEffect(() => {
    if (!authorization) return;
    const handler = (event: MessageEvent) => {
      const type = event?.data?.type;
      if (type === 'AGENT_LICENSE_CLOSE') {
        const botId = pendingAuthBotIdRef.current;
        if (!botId) return;
        pendingAuthBotIdRef.current = undefined;
        setAuthorization(undefined);
        setCopying(true);
        setPendingIssuedBotId(botId);
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [authorization]);

  useEffect(() => {
    if (!pendingIssuedBotId) return;
    let active = true;
    let terminal = false;
    // 自调度轮询:每轮 detail 结果返回后再等 5s 才发下一轮 —— 保证「等上次结果返回再请求」,
    // 不重叠(setInterval 不等 async,慢网会并发);同时不留 `polling` 守卫也能杜绝双触发。
    let pendingTimer: number | undefined;
    const poll = async () => {
      if (terminal || !active) return;
      // 单 bot 定向轮询(不受工作台 keyword/分页过滤影响)。
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
        setAuthorization(undefined);
        setCopying(false);
        onClose();
        toast.success(`${bot.name} 已复制`);
        await load();
        return;
      }
      // 未查到(404):5s 后再发下一轮。
      pendingTimer = window.setTimeout(poll, 5000);
    };
    // 首轮 poll 延迟 5s:create_job 刚起跑(detail-wait 触发那一刻),dst bot 行尚未建好,
    // 头几次必 404 —— 等 5s 再开始(然后每轮结果返回后 5s 发下一轮)。
    const startDelay = window.setTimeout(() => {
      void poll();
    }, 5000);
    const timeout = window.setTimeout(() => {
      if (active && !terminal) {
        terminal = true;
        setPendingIssuedBotId(undefined);
        setAuthorization(undefined);
        setCopying(false);
        onClose();
        toast.warning('新 Bot 仍在创建中,请稍后刷新查看');
        void load();
      }
    }, 60_000);
    return () => {
      active = false;
      window.clearTimeout(startDelay);
      if (pendingTimer) window.clearTimeout(pendingTimer);
      window.clearTimeout(timeout);
    };
  }, [pendingIssuedBotId, load, onClose]);

  const submitCopy = useCallback(
    async (input: BotCopyInput) => {
      setCopying(true);
      try {
        const result = await botWorkshopService.copyBot(input);
        if (result.type === 'authorization_required') {
          // 不走 auth-status 轮询(copy dst 无 auth-status)。AuthorizationPanel 只展示授权页;
          // detail-wait **不在此起** —— dst bot_id 暂存 ref,等 AgentPass 授权完成
          // (AGENT_LICENSE_CLOSE)才灌进 pendingIssuedBotId 起 detail-wait(避免 auth 前 404 轮询)。
          // copying=true 维持 busy,iframe 全屏期间浮窗不可关;CLOSE 后切「复制中」罩(awaitingDetail)。
          setAuthorization(result);
          setCopying(true);
          pendingAuthBotIdRef.current = result.botId;
          toast.success('复制已提交,请在授权页完成授权');
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
    [],
  );

  const cancelAuthorization = useCallback(() => {
    setAuthorization(undefined);
    setPendingIssuedBotId(undefined);
    setCopying(false);
  }, []);

  return {
    copying,
    // detail-wait 在跑(pendingIssuedBotId 已设)。区别于 copying(copyBot API in-flight 也真):
    // 复制浮窗只在 awaitingDetail 时弹「正在等待新 Bot 创建」罩,提交后、auth 出来前那段不弹。
    awaitingDetail: Boolean(pendingIssuedBotId),
    authorization,
    cancelAuthorization,
    submitCopy,
  };
}
