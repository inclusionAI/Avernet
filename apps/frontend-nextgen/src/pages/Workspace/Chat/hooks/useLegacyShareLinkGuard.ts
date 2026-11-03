// 老版分享链接落地守护（/workspace/chat）。
// 老版 /private-chat/chat?id=bot_id:owner_id 经域名重定向后落本页：route 契约已把含冒号的
// id 视作 bot 别名，有好友关系时 resolver 目录归类直接开会话（本 hook 不参与）。
// 本 hook 只兜"无好友关系"分支：目录 hydration 后选中仍不在目录 → 反查公开 Bot →
// 唤起好友申请确认弹窗（对齐老版"直接发起好友申请"，经用户确认后发出）；
// 反查无果或状态不可用 → 轻提示并清参回退对话页默认态。
import type { HumanBotActionContext } from '@/domain/collaborationSquare/types';
import type { PublicBot } from '@/domain/collaborationSquare/types';
import type { ConversationBotSection } from '@/domain/conversation/types';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import { collaborationSquareBotService } from '@/services/collaborationSquare/collaborationSquareService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useRef, useState } from 'react';

function readLegacyTargetIdFromLocation(): string | null {
  const id = new URLSearchParams(window.location.search).get('id');
  return id && id.includes(':') ? id : null;
}

export interface LegacyShareLinkGuard {
  legacyBot: PublicBot | null;
  submitting: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function useLegacyShareLinkGuard(input: {
  hydrated: boolean;
  selectedBotId: string | null;
  knownBotIds: ReadonlySet<string>;
  /** 好友 Bot 目录的 botId 集合：老链接命中好友后自动开启一个新会话（AC-P1）。 */
  friendBotIds: ReadonlySet<string>;
  /** 页内新建会话链路（列表前置新会话并选中）；未注入时仅定位不开会话。 */
  createSession?: (botId: string) => Promise<void>;
  /** 目标 Bot 的会话列表是否已完成加载（非 loading 且无错误）：在途列表响应会覆盖前置缓存，完成前暂缓创建。 */
  friendSessionsLoaded: boolean;
  /** 选中分组归类结果：归到 friend 分组稳定后才创建（创建链路的选中保护要求前后选中一致）。 */
  selectedSection: ConversationBotSection | null;
  userId: string | null;
}): LegacyShareLinkGuard {
  const { hydrated, selectedBotId, knownBotIds, friendBotIds, createSession, friendSessionsLoaded, selectedSection, userId } = input;
  // mount 一次性捕获老链接 id：URL 投影随后会把 id 规范化为 bot=，之后取不到原始参数。
  const [legacyTargetId] = useState(readLegacyTargetIdFromLocation);
  const [legacyBot, setLegacyBot] = useState<PublicBot | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const handledRef = useRef(false);
  // 目录 hook 初始空态三个 loading 均为 false，hydrated 首帧为假阳性（目录尚未发起加载、
  // knownBotIds 为空集）；必须等到"见过加载期之后的空闲"才可信，否则好友 Bot 会在目录
  // 就绪前被误判为无好友而弹出申请窗（V1 验收实测踩坑：弹窗与已开会话并存）。
  const sawDirectoryLoadingRef = useRef(false);

  const resetToDefault = useCallback(() => {
    setLegacyBot(null);
    useConversationStore.getState().selectConversation({
      botId: null,
      section: null,
      origin: 'mine',
      scope: 'all',
      friendUserId: null,
      sessionId: null,
    });
  }, []);

  useEffect(() => {
    if (handledRef.current || !legacyTargetId) return;
    if (!hydrated) {
      sawDirectoryLoadingRef.current = true; // 目录进入加载期：之后的空闲才代表数据就绪。
      return;
    }
    if (!sawDirectoryLoadingRef.current) return; // 初始假阳性空闲帧：目录尚未发起加载，跳过。
    if (selectedBotId !== legacyTargetId) return;
    if (knownBotIds.has(legacyTargetId)) return; // 目录命中=有好友关系，正常会话装配接管。
    handledRef.current = true;

    let cancelled = false;
    void (async () => {
      const context: HumanBotActionContext | undefined = userId
        ? { actorId: `human_${userId}`, userId }
        : undefined;
      const bot = await collaborationSquareBotService
        .resolvePublicBotById(legacyTargetId, context)
        .catch(() => null);
      if (cancelled) return;
      if (bot && (!bot.status || bot.status === 'ACTIVE')) {
        setLegacyBot(bot);
        return;
      }
      notifyError('该 Bot 已下线或已取消公开');
      resetToDefault();
    })();
    return () => {
      cancelled = true;
    };
  }, [handledRef, hydrated, knownBotIds, legacyTargetId, resetToDefault, selectedBotId, userId]);

  // 老版语义对齐（AC-P1）：好友命中的老链接落地后自动开启一个新会话——复用页内
  // "新建会话"链路（列表前置新会话并选中，与侧栏按钮同款语义）。
  const openedSessionRef = useRef(false);

  useEffect(() => {
    if (openedSessionRef.current || !legacyTargetId || !createSession) return;
    if (!hydrated) return;
    if (!sawDirectoryLoadingRef.current) return; // 目录就绪前的假阳性空闲帧不判定。
    if (selectedBotId !== legacyTargetId) return;
    if (!friendBotIds.has(legacyTargetId)) return; // 仅好友 Bot 自动开会话（老链接典型受众）。
    if (!friendSessionsLoaded) return; // 会话列表未就绪暂缓：新会话需前置进已加载的列表缓存。
    if (selectedSection !== 'friend') return; // 归类到好友分组稳定后才创建：避免创建期间 section 由 null 补成 'friend' 触发选中保护。
    openedSessionRef.current = true;
    // 创建成功/失败的 toast、列表前置与选中新会话均由该链路内聚处理。
    void createSession(legacyTargetId);
  }, [createSession, friendBotIds, friendSessionsLoaded, hydrated, legacyTargetId, selectedBotId, selectedSection]);

  const onConfirm = useCallback(() => {
    const bot = legacyBot;
    if (!bot || !userId || submitting) return;
    setSubmitting(true);
    void (async () => {
      try {
        const result = await collaborationSquareBotService.requestBotFriendship(
          bot.id,
          { actorId: `human_${userId}`, userId },
          bot.friendRequestBotId,
          { type: 'human', id: userId },
        );
        if (result.status === 'applying') notifySuccess('好友申请已提交');
        else if (result.status === 'friend') notifySuccess('好友关系已建立');
        else notifyError('当前未建立好友关系，申请未提交');
      } catch (error) {
        notifyError(error instanceof Error ? error.message : '发送申请失败，请稍后重试');
      } finally {
        setSubmitting(false);
        resetToDefault();
      }
    })();
  }, [legacyBot, resetToDefault, submitting, userId]);

  return { legacyBot, submitting, onClose: resetToDefault, onConfirm };
}
