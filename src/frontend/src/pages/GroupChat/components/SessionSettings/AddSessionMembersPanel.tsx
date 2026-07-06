/**
 * AddSessionMembersPanel - 会话设置 Drawer 的「添加会话成员」二级面板
 *
 * - Tab 切换：我的好友（actorKind=human） / 可协作 Bot（actorKind=bot）
 * - 名称模糊搜索
 * - 排除已在会话内（mode !== 'absent'）的 actor
 * - 上限：MAX_SESSION_MEMBERS − 当前活跃成员数
 * - 批量确认 → useSessionMembers.addSessionMembersBatch
 */

import BotAvatar from '@/components/BotAvatar';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useActor } from '@/hooks/useActor';
import { useSessionMembers } from '@/pages/GroupChat/hooks/useSessionMembers';
import * as BcnController from '@/services/backend-api/BcnController';
import { useBotNetworkStore } from '@/stores/botNetworkStore';
import { cn } from '@/utils/utils';
import { ArrowLeft, Check, Loader2, Plus, Search } from 'lucide-react';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ActorKind, GroupSession } from '../../types';
import { MAX_SESSION_MEMBERS } from './constants';

type Tab = 'friends' | 'collaborate';

interface CandidateBot {
  bot_uuid: string;
  bot_name: string;
  summary?: string;
  avatar_url?: string;
  is_online?: boolean;
  dynamic_status?: { status?: string };
}

interface AddSessionMembersPanelProps {
  session: GroupSession;
  /** 关闭面板（返回一级） */
  onBack: () => void;
}

const isBotOnline = (bot: CandidateBot): boolean => {
  if (bot.dynamic_status?.status === 'offline') return false;
  if (bot.is_online === false) return false;
  return true;
};

const AddSessionMembersPanel: React.FC<AddSessionMembersPanelProps> = ({
  session,
  onBack,
}) => {
  const driverBot = useBotNetworkStore((state) => state.driverBot);
  const { loadActors } = useActor();
  const { addSessionMembersBatch, isAdding } = useSessionMembers();

  const [tab, setTab] = useState<Tab>('friends');
  const [keyword, setKeyword] = useState('');
  const [selectedUuids, setSelectedUuids] = useState<string[]>([]);

  const [friendBots, setFriendBots] = useState<CandidateBot[]>([]);
  const [collaborateBots, setCollaborateBots] = useState<CandidateBot[]>([]);
  const [isLoadingFriends, setIsLoadingFriends] = useState(false);
  const [isLoadingCollaborate, setIsLoadingCollaborate] = useState(false);

  // Esc 触发返回
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onBack();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onBack]);

  const loadFriends = useCallback(async () => {
    if (!driverBot?.bot_uuid) return;
    setIsLoadingFriends(true);
    try {
      const res = await BcnController.getFriends({
        bot_uuid: driverBot.bot_uuid,
      });
      const list = res?.friends || res?.data || [];
      const bots = list
        .filter((b) => b.bot_uuid !== driverBot.bot_uuid)
        .map<CandidateBot>((b) => ({
          bot_uuid: b.bot_uuid,
          bot_name: b.name,
          summary: b.summary ?? undefined,
          is_online: b.is_online,
          dynamic_status: b.dynamic_status,
        }));
      setFriendBots(bots);
    } catch (err) {
      console.error('[AddSessionMembersPanel] load friends failed', err);
      setFriendBots([]);
    } finally {
      setIsLoadingFriends(false);
    }
  }, [driverBot?.bot_uuid]);

  const loadCollaborate = useCallback(async () => {
    if (!driverBot?.bot_uuid) return;
    setIsLoadingCollaborate(true);
    try {
      const res = await loadActors({
        currentBotUuid: driverBot.bot_uuid,
        cooperatableOnly: true,
        pageNo: 1,
        pageSize: 50,
      });
      const bots = (res.bots || [])
        .filter((b) => b.bot_uuid !== driverBot.bot_uuid)
        .map<CandidateBot>((b) => ({
          bot_uuid: b.bot_uuid,
          bot_name: b.bot_name || b.capabilities?.name || b.bot_uuid,
          summary: b.summary ?? b.capabilities?.description ?? undefined,
          avatar_url: b.avatar_url,
          is_online: (b as any).is_online,
          dynamic_status: b.dynamic_status,
        }));
      setCollaborateBots(bots);
    } catch (err) {
      console.error('[AddSessionMembersPanel] load collaborate failed', err);
      setCollaborateBots([]);
    } finally {
      setIsLoadingCollaborate(false);
    }
  }, [driverBot?.bot_uuid, loadActors]);

  useEffect(() => {
    if (tab === 'friends') loadFriends();
    else loadCollaborate();
  }, [tab, loadFriends, loadCollaborate]);

  const currentBots = tab === 'friends' ? friendBots : collaborateBots;
  const isLoading = tab === 'friends' ? isLoadingFriends : isLoadingCollaborate;

  const filteredBots = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return currentBots;
    return currentBots.filter(
      (b) =>
        b.bot_name.toLowerCase().includes(kw) ||
        (b.summary || '').toLowerCase().includes(kw),
    );
  }, [currentBots, keyword]);

  // 已在会话内（mode !== absent）的 actorId 集合
  const inSessionIds = useMemo(
    () =>
      new Set(
        session.members
          .filter((m) => m.mode !== 'absent')
          .map((m) => m.actorId),
      ),
    [session.members],
  );

  // 活跃成员数 → 计算剩余配额
  const activeCount = useMemo(
    () => session.members.filter((m) => m.mode !== 'absent').length,
    [session.members],
  );
  const remaining = Math.max(0, MAX_SESSION_MEMBERS - activeCount);

  const toggle = (bot: CandidateBot) => {
    setSelectedUuids((prev) => {
      if (prev.includes(bot.bot_uuid)) {
        return prev.filter((x) => x !== bot.bot_uuid);
      }
      if (prev.length >= remaining) return prev;
      return [...prev, bot.bot_uuid];
    });
  };

  const candidateRef = useRef<Map<string, CandidateBot>>(new Map());
  useEffect(() => {
    currentBots.forEach((b) => candidateRef.current.set(b.bot_uuid, b));
  }, [currentBots]);

  const handleConfirm = async () => {
    if (selectedUuids.length === 0 || isAdding) return;
    const actorKind: ActorKind = tab === 'friends' ? 'human' : 'bot';
    const items = selectedUuids
      .map((uuid) => candidateRef.current.get(uuid))
      .filter((b): b is CandidateBot => !!b)
      .map((b) => ({ actorId: b.bot_uuid, actorKind }));

    const result = await addSessionMembersBatch(session.sessionId, items);
    if (result.success > 0) {
      setSelectedUuids([]);
      onBack();
    }
  };

  const k = selectedUuids.length;
  const isMaxed = k >= remaining;

  return (
    <div className="flex-1 min-h-0 flex flex-col bg-white">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-100 flex-shrink-0">
        <button
          type="button"
          onClick={onBack}
          className="p-1 rounded-md hover:bg-slate-100 transition-colors"
          title="返回"
        >
          <ArrowLeft className="w-4 h-4 text-slate-500" />
        </button>
        <h2 className="text-base font-semibold text-slate-800">添加会话成员</h2>
        <span
          className={cn(
            'ml-auto text-xs font-medium px-2 py-0.5 rounded-full',
            isMaxed
              ? 'text-amber-600 bg-amber-50'
              : 'text-lavender-600 bg-lavender-50',
          )}
        >
          已选 {k} / {remaining}
        </span>
      </div>

      <div className="px-4 pt-3 flex-shrink-0">
        <div className="flex items-center bg-slate-100 rounded-lg p-0.5">
          <button
            type="button"
            onClick={() => setTab('friends')}
            className={cn(
              'flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all',
              tab === 'friends'
                ? 'bg-white text-slate-800 shadow-sm'
                : 'text-slate-500 hover:text-slate-700',
            )}
          >
            我的好友
          </button>
          <button
            type="button"
            onClick={() => setTab('collaborate')}
            className={cn(
              'flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all',
              tab === 'collaborate'
                ? 'bg-white text-slate-800 shadow-sm'
                : 'text-slate-500 hover:text-slate-700',
            )}
          >
            可协作 Bot
          </button>
        </div>
      </div>

      <div className="px-4 pt-2 flex-shrink-0">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input
            type="text"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索 Bot / 好友名称..."
            className="w-full pl-8 pr-3 py-1.5 text-xs bg-white border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-400 transition-all"
          />
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-auto px-4 py-2">
        {isLoading ? (
          <div className="flex items-center justify-center h-32 text-slate-400 gap-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span className="text-xs">加载中...</span>
          </div>
        ) : filteredBots.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-xs text-slate-400">
            {keyword.trim() ? '未找到匹配的候选' : '暂无可选候选'}
          </div>
        ) : (
          <div className="space-y-1">
            {filteredBots.map((bot) => {
              const inSession = inSessionIds.has(bot.bot_uuid);
              const selected = selectedUuids.includes(bot.bot_uuid);
              const online = isBotOnline(bot);
              const disabledByMax = isMaxed && !selected;
              const disabled = inSession || !online || disabledByMax;

              const reason = inSession
                ? '已在会话中'
                : !online
                ? '该 Bot 当前不可加入'
                : disabledByMax
                ? '已达上限'
                : '';

              const Row = (
                <div
                  className={cn(
                    'flex items-center gap-2.5 px-2.5 py-2 rounded-lg transition-colors',
                    disabled
                      ? 'opacity-60 cursor-not-allowed'
                      : selected
                      ? 'bg-lavender-50/70 cursor-pointer'
                      : 'hover:bg-slate-50 cursor-pointer',
                  )}
                  onClick={() => {
                    if (disabled) return;
                    toggle(bot);
                  }}
                >
                  <BotAvatar
                    type="assistant"
                    size="sm"
                    name={bot.bot_name}
                    botId={bot.bot_uuid.split(':')[0]}
                    avatarUrl={bot.avatar_url}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      <span className="text-sm font-medium text-slate-800 truncate">
                        {bot.bot_name}
                      </span>
                      {online ? (
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-green-50 text-green-600 flex-shrink-0">
                          在线
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-medium bg-slate-100 text-slate-400 flex-shrink-0">
                          离线
                        </span>
                      )}
                    </div>
                    {bot.summary && (
                      <p className="text-xs text-slate-400 truncate leading-tight">
                        {bot.summary}
                      </p>
                    )}
                  </div>

                  {inSession ? (
                    <span className="text-xs text-slate-400 flex-shrink-0">
                      已在会话
                    </span>
                  ) : (
                    <div
                      className={cn(
                        'w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 transition-all',
                        selected
                          ? 'bg-lavender-500 text-white'
                          : disabled
                          ? 'border border-slate-200 text-slate-300'
                          : 'border border-slate-300 text-slate-400',
                      )}
                    >
                      {selected ? (
                        <Check className="w-3 h-3" />
                      ) : (
                        <Plus className="w-3 h-3" />
                      )}
                    </div>
                  )}
                </div>
              );

              if (disabled && reason && !inSession) {
                return (
                  <TooltipProvider key={bot.bot_uuid} delayDuration={100}>
                    <Tooltip>
                      <TooltipTrigger asChild>{Row}</TooltipTrigger>
                      <TooltipContent side="top" className="text-xs">
                        {reason}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                );
              }
              return <React.Fragment key={bot.bot_uuid}>{Row}</React.Fragment>;
            })}
          </div>
        )}
      </div>

      <div className="px-4 py-3 border-t border-slate-100 flex-shrink-0">
        <button
          type="button"
          onClick={handleConfirm}
          disabled={k === 0 || isAdding}
          className={cn(
            'w-full px-4 py-2 rounded-lg text-sm font-medium transition-colors',
            k === 0 || isAdding
              ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
              : 'bg-lavender-500 text-white hover:bg-lavender-600',
          )}
        >
          {isAdding ? (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              添加中...
            </span>
          ) : k === 0 ? (
            '添加成员'
          ) : (
            `添加 ${k} 人`
          )}
        </button>
      </div>
    </div>
  );
};

export default AddSessionMembersPanel;
