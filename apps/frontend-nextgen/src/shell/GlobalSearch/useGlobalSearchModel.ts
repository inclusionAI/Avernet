// 全局搜索数据模型（设计稿 artboard-016「Dialog」搜索弹窗的三路数据装配）。
// 数据源全部复用既有 service，不新增后端接口：
// - Bot：botSessionService.listOwnedBotsWithMeta（我的 Bot，1 请求，客户端过滤）；
// - 对话：逐 Bot listSessions（每 Bot 1 请求、page_size 50，弹窗打开后并行拉取一次并缓存）；
// - 协作群：groupService.loadGroups（服务端 q 模糊匹配，输入防抖触发）。
// 空关键词 = 「最近」视角（原样返回最近 50 项），对齐设计稿无输入时的 Suggestions 列表。
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import type { IdentityView } from '@/domain/collaboration';
import type { GroupView } from '@/domain/collaboration';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { groupService } from '@/services/workspace/groupService';
import { useEffect, useMemo, useRef, useState } from 'react';

/** 群类型短签（与 GroupSidebar 行标签同词表；shell 层不反向依赖 pages，就近维护）。 */
const GROUP_KIND_LABEL: Record<GroupView['kind'], string> = {
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协同',
};

/** 逐 Bot 拉会话的并行上限：防止 Bot 数量异常膨胀时打开弹窗打爆网关。 */
const SESSION_FETCH_BOT_LIMIT = 20;

/** 群列表服务端 q 搜索的防抖间隔（ms）。 */
const GROUP_QUERY_DEBOUNCE_MS = 300;

export type GlobalSearchKind = 'all' | 'bot' | 'conversation' | 'group';

export interface GlobalSearchResult {
  key: string;
  kind: Exclude<GlobalSearchKind, 'all'>;
  /** 行标题：Bot/群名称 或 会话标题。 */
  title: string;
  /** 副行片段（设计稿 12px 灰描，行内「·」分隔）：类型打头 + 详情补充。 */
  metaParts: string[];
  /** Bot 行头像（Bot 可选带 avatarUrl；无头像时由弹窗回退类型图标）。 */
  avatarUrl?: string;
  /** 行点击深链所需 id（见弹窗 navigateForResult）。 */
  botId?: string;
  sessionId?: string;
  groupId?: string;
}

interface SessionEntry {
  session: BotChatSessionView;
  bot: ChatBotView;
}

function matches(query: string, haystacks: (string | undefined)[]): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return haystacks.some((h) => !!h && h.toLowerCase().includes(q));
}

/** 全局搜索数据装配：open=false 期间不发起任何请求。 */
export function useGlobalSearchModel(open: boolean) {
  const { identity } = useHumanIdentity();
  const userId = identity?.userId ?? null;
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<GlobalSearchKind>('all');
  const [bots, setBots] = useState<ChatBotView[]>([]);
  const [sessions, setSessions] = useState<SessionEntry[]>([]);
  const [groups, setGroups] = useState<GroupView[]>([]);
  const [botsLoading, setBotsLoading] = useState(false);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [groupsLoading, setGroupsLoading] = useState(false);
  // 会话缓存只随弹窗打开轮次失效：一条关键词变化不重复打逐 Bot 会话接口（客户端过滤即可）。
  const sessionsBotsMemo = useRef<string>('');

  useEffect(() => {
    if (!open) return undefined;
    // 重新打开回到干净态：清空关键词并复位筛选。
    setQuery('');
    setFilter('all');
    return undefined;
  }, [open]);

  // Bot 列表（弹窗打开时拉一次）。
  useEffect(() => {
    if (!open || !userId) return;
    let cancelled = false;
    setBotsLoading(true);
    void botSessionService.listOwnedBotsWithMeta(userId).then((res) => {
      if (cancelled) return;
      setBots(res.ok ? res.data.bots : []);
      setBotsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [open, userId]);

  // 会话列表（Bot 到位后逐 Bot 并行拉一次；轮次内关键词变化不重拉）。
  useEffect(() => {
    if (!open || !userId || botsLoading || bots.length === 0) {
      if (!open) {
        sessionsBotsMemo.current = '';
        setSessions([]);
      }
      return undefined;
    }
    const memoKey = bots.map((b) => b.botId).join('|');
    if (memoKey === sessionsBotsMemo.current) return undefined;
    sessionsBotsMemo.current = memoKey;
    let cancelled = false;
    setSessionsLoading(true);
    Promise.all(
      bots.slice(0, SESSION_FETCH_BOT_LIMIT).map(async (bot) => {
        const res = await botSessionService.listSessions(bot, userId).catch(() => null);
        return res && res.ok ? res.data.map((session) => ({ session, bot })) : [];
      }),
    ).then((pages) => {
      if (cancelled) return;
      setSessions(pages.flat().sort((a, b) => b.session.gmtModified.localeCompare(a.session.gmtModified)));
      setSessionsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [open, userId, bots, botsLoading]);

  // 群列表（服务端 q 模糊匹配；空关键词拉最近群）——防抖后按 open+关键词触发。
  const [debouncedQuery, setDebouncedQuery] = useState('');
  useEffect(() => {
    if (!open) {
      setDebouncedQuery('');
      return undefined;
    }
    const timer = window.setTimeout(() => setDebouncedQuery(query), GROUP_QUERY_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [open, query]);

  useEffect(() => {
    if (!open || !userId) return undefined;
    let cancelled = false;
    setGroupsLoading(true);
    const identityView: IdentityView = {
      id: userId,
      kind: 'user',
      displayName: identity?.displayName ?? '',
      avatarUrl: identity?.avatarUrl,
      online: true,
    };
    void groupService.loadGroups(identityView, { q: debouncedQuery }).then((res) => {
      if (cancelled) return;
      setGroups(res.ok ? res.data : []);
      setGroupsLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [open, userId, debouncedQuery, identity?.displayName, identity?.avatarUrl]);

  const results = useMemo<GlobalSearchResult[]>(() => {
    const typeFilter = (kind: GlobalSearchResult['kind']) => (filter === 'all' || filter === kind ? kind : null);
    const sessionRows: GlobalSearchResult[] = typeFilter('conversation')
      ? sessions
          .filter((e) => matches(query, [e.session.title, e.bot.displayName]))
          .map((e) => ({
            key: `conversation:${e.bot.botId}:${e.session.sessionId}`,
            kind: 'conversation' as const,
            title: e.session.title,
            metaParts: ['对话', e.bot.displayName],
            botId: e.bot.botId,
            sessionId: e.session.sessionId,
          }))
      : [];
    const groupRows: GlobalSearchResult[] = typeFilter('group')
      ? groups
          .filter((g) => matches(query, [g.name, g.groupId]))
          .map((g) => ({
            key: `group:${g.groupId}`,
            kind: 'group' as const,
            title: g.name,
            // 设计副行「类型 · 详情」结构：协作群带类型短签 + 成员数。
            metaParts: ['协作群', GROUP_KIND_LABEL[g.kind], `${g.participantCount} 名成员`],
            groupId: g.groupId,
          }))
      : [];
    const botRows: GlobalSearchResult[] = typeFilter('bot')
      ? bots
          .filter((b) => matches(query, [b.displayName, b.botId, b.engine]))
          .map((b) => ({
            key: `bot:${b.botId}`,
            kind: 'bot' as const,
            title: b.displayName,
            metaParts: b.engine ? ['我的Bot', b.engine] : ['我的Bot'],
            avatarUrl: b.avatarUrl,
            botId: b.botId,
          }))
      : [];
    // 「全部」排序对齐设计稿 Suggestions：对话在前（最近优先），协作群次之，Bot 收尾。
    return filter === 'all' ? [...sessionRows, ...groupRows, ...botRows] : [...botRows, ...sessionRows, ...groupRows];
  }, [bots, groups, sessions, query, filter]);

  return {
    query,
    setQuery,
    filter,
    setFilter,
    results,
    // 三路中任一路在途即算 loading（骨架占位）；分三批到位时结果行渐进呈现。
    isLoading: botsLoading || sessionsLoading || groupsLoading,
  };
}