// @asset-migrated: teamclaw 自研资产
/**
 * GroupSessionView —— coop_group 节点的执行详情：群成员 + 群消息明细。
 * GroupDrillDownPanel —— 多个 coop_group 节点的左侧并列 Tab 容器。
 */
import React, { useEffect, useState } from 'react';
import { ArrowLeft, Close, Users } from './icons';
import { Empty, SectionCard } from './theme';
import { C } from './tokens';
import { TruncatedText } from './TruncatedText';
import type { TaskNodeView } from './types';
// 复用主屏对话同款 MarkdownRenderer（@tc-chat/ui）：统一适配 markdown / 代码块 / JSON / 列表等格式，
// 不再把 ```json 等内容当纯文本原样输出。
import { getCapabilities } from '@/capabilities';
import { MarkdownRenderer } from '@tc-chat/ui/es/MarkdownRender';
import styled from 'styled-components';
import { renderableSource, unwrapHttpEnvelope } from './outputEnvelope';
import { truncateText } from './text';

/** 解析当前登录用户的纯工号(供协作群视角匹配群成员归属)。
 * panel params 未下发 userId(后端 opening_message 不带),回退能力层 getHumanIdentity 取人类身份工号;
 * 与群成员 actor_id 末段(:工号)比对,定位本人创建的 bot 以作 view_bot_id。
 * 资产目录禁止反向 import stores/domain,故统一只经 @/capabilities 取人类身份。 */
function resolveCurrentUserId(preferred?: string): string {
  const explicit = (preferred ?? '').trim();
  if (explicit && explicit.toLowerCase() !== 'me') return explicit;
  const human = getCapabilities().getHumanIdentity();
  if (human.status === 'available' && human.value) {
    const uid = (human.value.userId ?? '').trim();
    if (uid) return uid;
  }
  return '';
}

function isMockId(id: string): boolean {
  return id.startsWith('mock_');
}

async function fetchGroupDetail(groupId: string, bcsBaseUrl: string) {
  if (isMockId(groupId)) return null;
  const url = bcsBaseUrl
    ? `${bcsBaseUrl}/groups/${encodeURIComponent(groupId)}`
    : `/openapi/v1/collaboration/groups/${encodeURIComponent(groupId)}`;
  const resp = await fetch(url, { credentials: 'include' });
  if (!resp.ok) throw new Error(`群详情请求失败（${resp.status}）`);
  const raw = bcsBaseUrl ? await resp.json() : (await resp.json())?.data;
  if (!raw) return null;
  return {
    group_id: raw.group_id ?? raw.id ?? groupId,
    name: raw.name ?? raw.label ?? raw.group_name ?? '',
    status: raw.status ?? 'unknown',
    participants: (raw.participants ?? []).map((p: Record<string, unknown>) => ({
      actor_id: (p.actor_id as string) ?? (p.bot_uuid as string) ?? (p.bot_id as string) ?? '',
      actor_kind: p.actor_kind ?? 'bot',
      name: (p.name as string) ?? (p.bot_name as string) ?? (p.actor_id as string) ?? (p.bot_uuid as string) ?? '',
      role: p.role ?? 'worker',
      mode: p.mode ?? 'auto',
    })),
  };
}

/** 兼容群消息（sender/content/message_type/bot_name/timestamp）与单聊消息（message_id/role/content/gmt_create）。 */
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function extractMessageList(value: unknown): Record<string, unknown>[] {
  const payload = unwrapHttpEnvelope(value);
  if (Array.isArray(payload)) return payload.filter(isRecord);
  if (!isRecord(payload)) return [];

  // 兼容分页 items、messages 以及原生 response.result 三种列表载荷。
  for (const key of ['items', 'messages', 'result']) {
    if (Array.isArray(payload[key])) return payload[key].filter(isRecord);
  }
  return [];
}

function normalizeMessage(m: Record<string, unknown>): GroupMessage {
  const role = m.role as string | undefined;
  const messageType = (m.message_type as string) ?? (role === 'user' ? 'human' : 'assistant');
  const ts = m.timestamp ?? m.gmt_create;
  return {
    id: (m.id as string) ?? (m.message_id as string) ?? String(m.gmt_create ?? Math.random()),
    sender: (m.sender as string) ?? (role === 'user' ? 'user' : 'bot'),
    content: (m.content as string) ?? '',
    message_type: messageType,
    bot_name: (m.bot_name as string) ?? undefined,
    role: role ?? undefined,
    timestamp: typeof ts === 'number' ? ts : ts ? new Date(ts as string).getTime() : Date.now(),
  };
}

/** 拉取会话消息——单聊与协作群走不同端点(切勿统一为一个):
 * - 单聊(run_mode=single_bot, session=agent:main:session:...):
 *   GET /openapi/v1/bots/{realBotId无后缀}/sessions/{sessionId}/messages?page=1&page_size=100&user_id&owner_id
 *   realBotId/owner_id 由 assignee 按 ':' 拆分(去 :userId 后缀);user_id 取当前用户(纯工号)。
 * - 协作群(run_mode=coop_group, session=bcs_grp_xxx:round):
 *   GET /openapi/v1/collaboration/sessions/{sessionId}/messages?limit=50&view_bot_id={归属本人bot完整actor_id}
 *   view_bot_id 必须是群参与者中归属本人的 bot,否则 40300 "not a Session Participant"。
 */
async function fetchSessionMessages(
  sessionId: string,
  isGroup: boolean,
  botId?: string | null,
  userId?: string,
  viewBotId?: string,
): Promise<GroupMessage[]> {
  if (isMockId(sessionId)) return [];
  let url: string;
  if (isGroup) {
    const query = new URLSearchParams({ limit: '50' });
    if (viewBotId) query.set('view_bot_id', viewBotId);
    url = `/openapi/v1/collaboration/sessions/${encodeURIComponent(sessionId)}/messages?${query}`;
  } else {
    // 单聊:bots 会话消息端点;path 段为 bot 内部 id(去 :userId 后缀)。
    // user_id/owner_id 必须取「会话归属人」(session_id 里的 :user:xxx,即 bot 的 owner),而非当前登录人——
    // bots 端点按 user_id 圈定 bot 查找范围,用登录人去查他人的 bot 会 404(跨用户子任务节点)。
    if (!botId) return [];
    const idx = botId.lastIndexOf(':');
    const realBotId = idx >= 0 ? botId.slice(0, idx) : botId;
    const sessionUser = sessionId.match(/:user:([^:]+)$/)?.[1] ?? '';
    const botOwner = idx >= 0 ? botId.slice(idx + 1) : '';
    const scopeUser = sessionUser || botOwner || userId || '';
    const query = new URLSearchParams({ page: '1', page_size: '100' });
    if (scopeUser) {
      query.set('user_id', scopeUser);
      query.set('owner_id', scopeUser);
    }
    url = `/openapi/v1/bots/${encodeURIComponent(realBotId)}/sessions/${encodeURIComponent(
      sessionId,
    )}/messages?${query}`;
  }
  const resp = await fetch(url, { credentials: 'include' });
  if (!resp.ok) throw new Error(`会话消息请求失败（${resp.status}）`);
  const json = await resp.json();
  // 原生接口可能返回 { data: { result: [...] } }，统一剥离 response 信封，
  // 再兼容 BCS raw / 分页 items / messages 列表，避免把 data.result 当成空列表。
  const list = extractMessageList(json);
  return list.map(normalizeMessage);
}

const ROLE_LABELS: Record<string, string> = {
  driver: '驱动者',
  manager: '管理者',
  worker: '执行者',
  consultant: '顾问',
  observer: '观察者',
};

export interface GroupParticipant {
  actor_id: string;
  actor_kind: string;
  name: string;
  role: string;
  mode: string;
}

interface GroupData {
  group_id: string;
  name: string;
  status: string;
  participants: GroupParticipant[];
}

/** 协作群 Owner 使用 master bot，不使用 group_id 或节点 assignee ID。 */
export function resolveMasterBot(
  participants: GroupParticipant[],
  fallbackName?: string | null,
): GroupParticipant | null {
  return (
    participants.find((participant) => participant.actor_kind === 'bot' && participant.role === 'manager') ??
    participants.find((participant) => participant.actor_kind === 'bot' && participant.role === 'driver') ??
    (fallbackName
      ? { actor_id: fallbackName, actor_kind: 'bot', name: fallbackName, role: 'manager', mode: 'auto' }
      : null)
  );
}

/** 群成员展示顺序：master/manager/driver 优先，worker 次之，其余按原序；同优先级保持稳定。 */
const MEMBER_ROLE_PRIORITY: Record<string, number> = { manager: 0, master: 0, driver: 0, worker: 1 };
const memberRoleRank = (role: string): number =>
  Object.prototype.hasOwnProperty.call(MEMBER_ROLE_PRIORITY, role) ? MEMBER_ROLE_PRIORITY[role] : 2;
export function sortGroupMembers(participants: GroupParticipant[]): GroupParticipant[] {
  return [...participants].sort((a, b) => memberRoleRank(a.role) - memberRoleRank(b.role));
}

/** 从 actor_id（形如 internal:user_id）解析 owner_id（末段）。 */
function actorOwnerId(actorId: string): string {
  const idx = actorId.lastIndexOf(':');
  return idx >= 0 ? actorId.slice(idx + 1) : '';
}

/** 群成员中由当前用户创建的 bot：actor_kind=bot 且 owner_id === 本人。 */
function isOwnedBot(participant: GroupParticipant, userId?: string): boolean {
  return participant.actor_kind === 'bot' && Boolean(userId) && actorOwnerId(participant.actor_id) === userId;
}

/**
 * 选取查询群消息的视角 bot：仅限当前用户自己创建的 bot（有权限）。
 * 优先 worker（任务执行侧的本人 bot），次选归属本人的 master/manager；都没有则返回 null → 无查询权限。
 */
export function resolveOwnedViewBot(participants: GroupParticipant[], userId?: string): GroupParticipant | null {
  const owned = participants.filter((p) => isOwnedBot(p, userId));
  if (!owned.length) return null;
  return (
    owned.find((p) => p.role === 'worker') ?? owned.find((p) => p.role === 'manager' || p.role === 'driver') ?? owned[0]
  );
}

export interface GroupMessage {
  sender: string;
  content: string;
  message_type: string;
  bot_name?: string;
  role?: string;
  timestamp: number | string;
  id: string;
}

/** 群聊消息流中的系统事件、system Bot 回复和注入给 Bot 的 GroupContext 不是用户可读消息；
 *  单聊执行流中的 tool_result/工具调用、以及无文本的空回合同样不是可读对话，一并过滤。 */
export function isNonMessageGroupContent(
  message: Pick<GroupMessage, 'message_type' | 'content' | 'sender' | 'bot_name' | 'role'>,
): boolean {
  const role = String(message.role ?? '')
    .trim()
    .toLowerCase();
  // 工具调用/工具结果不是用户可读对话
  if (role === 'tool_result' || role === 'tool_call' || role === 'tool') return true;
  // 空内容（如 assistant 仅触发工具、无文本回复的那一空回合）不展示
  if (!String(message.content ?? '').trim()) return true;
  const messageType = String(message.message_type ?? '')
    .trim()
    .toLowerCase();
  const sender = String(message.bot_name ?? message.sender ?? '')
    .trim()
    .toLowerCase();
  const normalizedSender = sender.replace(/[\s_-]+/g, '');
  if (messageType === 'system' || messageType.startsWith('system_') || messageType.startsWith('system-')) return true;
  if (normalizedSender === 'system' || normalizedSender === 'systembot') return true;

  const content = String(message.content ?? '')
    .trim()
    .toLowerCase();
  return (
    content.includes('<groupcontext>') ||
    content.includes('</groupcontext>') ||
    content.includes('当前你在 bcn 群聊中') ||
    content.includes('## 群聊信息') ||
    content.includes('## 参与者')
  );
}

export function filterGroupMessages(messages: GroupMessage[]): GroupMessage[] {
  return messages.filter((message) => !isNonMessageGroupContent(message));
}

/** 会话接口返回顺序不稳定，尤其协作群接口常按最新消息在前返回；展示统一按时间递增。 */
export function sortMessagesByTimestamp(messages: GroupMessage[]): GroupMessage[] {
  return messages
    .map((message, index) => ({ message, index }))
    .sort((a, b) => {
      const aTime = new Date(a.message.timestamp).getTime();
      const bTime = new Date(b.message.timestamp).getTime();
      const aSortable = Number.isNaN(aTime) ? Number.MAX_SAFE_INTEGER : aTime;
      const bSortable = Number.isNaN(bTime) ? Number.MAX_SAFE_INTEGER : bTime;
      return aSortable - bSortable || a.index - b.index;
    })
    .map(({ message }) => message);
}

function senderColor(sender: string): string {
  const palette = [C.primary, '#6366F1', '#0EA5E9', C.success, C.warning];
  const score = Array.from(sender).reduce((sum, char) => sum + char.charCodeAt(0), 0);
  return palette[score % palette.length];
}

function formatMessageTime(timestamp: number | string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return String(timestamp || '');
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

const MESSAGE_CONTENT_FONT_SIZE = 12;
const MESSAGE_DISPLAY_CHAR_LIMIT = 200;

export function shouldCollapseMessage(content: string): boolean {
  return Array.from(content.trim()).length > MESSAGE_DISPLAY_CHAR_LIMIT;
}

/**
 * 节点下钻消息与副屏的紧凑信息层级保持一致。
 * MarkdownRenderer 默认正文为 15px，且标题/行内代码会进一步放大，
 * 不能只依赖气泡容器的 fontSize 继承，因此在消息内容边界统一收敛字号。
 */
const MessageContent = styled.div`
  --aix-markdown-font-size: ${MESSAGE_CONTENT_FONT_SIZE}px;
  font-size: ${MESSAGE_CONTENT_FONT_SIZE}px;
  line-height: 1.6;

  h1,
  h2,
  h3,
  h4,
  h5,
  h6 {
    font-size: ${MESSAGE_CONTENT_FONT_SIZE}px !important;
    line-height: 1.6;
    margin: 8px 0 4px;
  }

  p,
  li,
  blockquote,
  th,
  td {
    font-size: ${MESSAGE_CONTENT_FONT_SIZE}px;
  }

  code {
    font-size: 11px !important;
  }
`;

const ChatBubble: React.FC<{ msg: GroupMessage }> = ({ msg }) => {
  const sender = msg.bot_name ?? msg.sender ?? '未知成员';
  const isHuman = msg.message_type.toLowerCase() === 'human';
  const kindLabel = isHuman ? '用户' : 'Bot';
  const [expanded, setExpanded] = useState(false);

  // 过滤函数已经在入状态前执行，这里保留防御性判断，避免异常数据漏出。
  if (isNonMessageGroupContent(msg)) return null;

  const avatarColor = isHuman ? C.surfaceAlt : senderColor(sender);
  const collapsible = shouldCollapseMessage(msg.content);
  const collapsed = collapsible && !expanded;
  const renderedContent = renderableSource(msg.content) ?? msg.content;
  const displayContent = collapsed ? truncateText(renderedContent, MESSAGE_DISPLAY_CHAR_LIMIT) : renderedContent;
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '2px 0' }}>
      <div
        style={{
          width: 28,
          height: 28,
          borderRadius: '50%',
          background: avatarColor,
          color: isHuman ? C.textPrimary : '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          fontSize: 11,
          fontWeight: 650,
          boxShadow: `0 0 0 3px ${isHuman ? C.surfaceAlt : `${avatarColor}18`}`,
        }}
      >
        {sender.slice(0, 1)}
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
          <span
            style={{
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              color: isHuman ? C.textSecondary : avatarColor,
              fontSize: 10,
              fontWeight: 650,
            }}
          >
            {sender}
          </span>
          <span
            style={{
              padding: '1px 4px',
              borderRadius: 3,
              background: isHuman ? C.surfaceAlt : `${avatarColor}14`,
              color: isHuman ? C.textSecondary : avatarColor,
              fontSize: 9,
              flexShrink: 0,
            }}
          >
            {kindLabel}
          </span>
          <span style={{ marginLeft: 'auto', color: C.textMuted, fontSize: 9, whiteSpace: 'nowrap', flexShrink: 0 }}>
            {formatMessageTime(msg.timestamp)}
          </span>
        </div>
        <MessageContent
          data-testid="task-panel-message-content"
          style={{
            padding: '9px 11px',
            border: `1px solid ${isHuman ? C.border : `${avatarColor}28`}`,
            borderRadius: '4px 10px 10px 10px',
            background: isHuman ? C.surfaceRaised : C.surface,
            boxShadow: '0 2px 8px rgba(29, 33, 41, 0.04)',
            color: C.textPrimary,
            fontSize: MESSAGE_CONTENT_FONT_SIZE,
            lineHeight: 1.6,
            overflowWrap: 'anywhere',
            paddingBottom: collapsed ? 34 : 9,
            overflow: 'visible',
            position: 'relative',
          }}
        >
          {msg.content.trim() ? (
            <MarkdownRenderer content={displayContent} />
          ) : (
            <span style={{ color: C.textMuted }}>（空消息）</span>
          )}
          {collapsed && (
            <button
              type="button"
              aria-label="展开消息"
              onClick={() => setExpanded(true)}
              style={{
                position: 'absolute',
                left: 0,
                right: 0,
                bottom: 0,
                height: 38,
                border: 0,
                background: `linear-gradient(to bottom, transparent, ${isHuman ? C.surfaceRaised : C.surface})`,
                color: C.primary,
                fontSize: 11,
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'flex-end',
                justifyContent: 'center',
                paddingBottom: 2,
              }}
            >
              展开全部 ▾
            </button>
          )}
        </MessageContent>
        {collapsible && expanded && (
          <button
            type="button"
            aria-label="收起消息"
            onClick={() => setExpanded(false)}
            style={{
              alignSelf: 'flex-start',
              marginTop: 5,
              padding: 0,
              border: 0,
              background: 'transparent',
              color: C.primary,
              fontSize: 11,
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            收起 ▴
          </button>
        )}
      </div>
    </div>
  );
};

export const GroupSessionView: React.FC<{
  node: TaskNodeView;
  bcsBaseUrl: string;
  apiBaseUrl: string;
  userId?: string;
  onBack: () => void;
}> = ({ node, bcsBaseUrl, apiBaseUrl, userId, onBack }) => {
  const groupId = node.groupId;
  const sessionId = node.sessionId;
  // 下钻通道按物理 session 形态判定:协作群 session_id 形如 bcs_grp_xxx:round 或存在 groupId。
  // run_mode 可能被 actual_run_mode(权限绕过真实模式)覆盖,不能再据 run_mode 选消息端点。
  const isGroup = node.runMode === 'coop_group' || Boolean(groupId) || (sessionId?.startsWith('bcs_grp_') ?? false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [group, setGroup] = useState<GroupData | null>(null);
  const [messages, setMessages] = useState<GroupMessage[]>([]);
  const [membersExpanded, setMembersExpanded] = useState(false);
  const [noMessagePermission, setNoMessagePermission] = useState(false);

  const assignee = node.assignee;
  useEffect(() => {
    if (!sessionId) {
      setError('节点缺少 session_id，无法下钻');
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNoMessagePermission(false);
    // group_id 非空（群执行）→ 查群成员（预发 OpenAPI；传入 bcsBaseUrl 时兼容 BCS raw）
    // group_id 空（单 bot）→ bot 信息由后端 dashboard 返回（待实现），本地用 node executor 兜底
    const memberPromise = groupId
      ? fetchGroupDetail(groupId, bcsBaseUrl)
      : Promise.resolve(
          isGroup && assignee
            ? {
                group_id: '',
                name: node.executor ?? assignee,
                status: 'active',
                participants: [
                  {
                    actor_id: assignee,
                    actor_kind: 'bot',
                    name: node.executor ?? assignee,
                    role: 'worker',
                    mode: 'auto',
                  },
                ],
              }
            : null,
        );
    // 消息可见性:
    // - 协作群:需以本人创建的 bot 身份查看,先 await 群详情取群成员中归属本人的 bot 作 view_bot_id;无则置无权限态。
    // - 单聊:走 bots 端点;若会话归属人 ≠ 当前登录人(跨用户 bot,bots 端点会 403)→ 置无权限态,不发起请求。
    (async () => {
      try {
        const g = await memberPromise;
        if (cancelled) return;
        const effectiveUserId = resolveCurrentUserId(userId);
        // 协作群:以群成员中归属本人(bot owner 工号 === 当前登录人)的 bot 作 view_bot_id;无则无查询权限。
        //   view_bot_id 不能用 group_id 或 assignee(group_id 形态)冒充,群消息端点会 40300 拒绝。
        const ownedViewBot = isGroup ? resolveOwnedViewBot(g?.participants ?? [], effectiveUserId) : null;
        // 有群详情(真实群成员)且无归属本人 bot → 无查询权限;无 group_id(根节点仅 sessionId,无群成员)
        // 时回退节点 assignee 作为视角 bot(节点 bot 多为本人 master/driver),避免群消息端点 40300。
        const viewBotId = isGroup ? ownedViewBot?.actor_id ?? (groupId ? '' : assignee ?? '') : '';
        // 单聊会话归属人(session_id 里的 :user:xxx,即 bot owner);与当前登录人不符 → 跨用户,无权查看。
        const singleSessionUser = !isGroup ? sessionId.match(/:user:([^:]+)$/)?.[1] ?? '' : '';
        const crossUserSingle =
          !isGroup && singleSessionUser !== '' && Boolean(effectiveUserId) && singleSessionUser !== effectiveUserId;
        const canViewMessages = isGroup ? Boolean(viewBotId) : !crossUserSingle;
        const msgs = canViewMessages
          ? await fetchSessionMessages(sessionId, isGroup, assignee, effectiveUserId, viewBotId || undefined)
          : [];
        if (cancelled) return;
        setGroup(g);
        setNoMessagePermission(!canViewMessages);
        setMessages(sortMessagesByTimestamp(filterGroupMessages(msgs as GroupMessage[])));
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : '加载执行会话信息失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [groupId, sessionId, bcsBaseUrl, apiBaseUrl, assignee, userId]);

  const memberCount = group?.participants.length ?? 0;
  const msgCount = messages.length;
  const groupName = node.name || group?.name || (isGroup ? '协作群执行详情' : '执行会话详情');
  const masterBot = resolveMasterBot(group?.participants ?? [], isGroup ? null : node.executor);
  // 群成员展示顺序：master/manager 在前、worker 在后，其余保持稳定序。
  const sortedMembers = group ? sortGroupMembers(group.participants) : [];
  const memberLabel = isGroup ? '群成员' : '执行者';
  const messageLabel = isGroup ? '群消息明细' : '对话消息';
  const sessionTypeLabel = isGroup ? '群聊' : '单聊';
  const ownerName = isGroup ? masterBot?.name ?? node.executor ?? '未识别' : node.executor ?? '未识别';

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        background: C.surface,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 9,
          padding: '14px 14px 12px',
          borderBottom: `1px solid ${C.border}`,
          flexShrink: 0,
        }}
      >
        <button
          type="button"
          aria-label="返回任务节点"
          onClick={onBack}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 28,
            height: 28,
            border: 0,
            borderRadius: 7,
            background: 'transparent',
            color: C.textSecondary,
            cursor: 'pointer',
            flexShrink: 0,
          }}
        >
          <ArrowLeft size={17} />
        </button>
        <div
          style={{
            width: 34,
            height: 34,
            borderRadius: '50%',
            background: node.executorColor ?? C.primary,
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            fontSize: 14,
            fontWeight: 650,
          }}
        >
          {(node.executor ?? groupName).slice(0, 1)}
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <TruncatedText
            value={groupName}
            maxLength={20}
            style={{
              color: C.textPrimary,
              fontSize: 14,
              fontWeight: 650,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          />
          <div
            style={{
              marginTop: 3,
              color: C.textSecondary,
              fontSize: 11,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {sessionTypeLabel} · Owner: {ownerName}
          </div>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '10px 14px 18px' }}>
        {loading ? (
          <div style={{ padding: '42px 0', color: C.textSecondary, fontSize: 12, textAlign: 'center' }}>
            正在加载执行信息…
          </div>
        ) : error ? (
          <Empty description={error} minHeight={120} />
        ) : (
          <>
            <button
              type="button"
              aria-expanded={membersExpanded}
              onClick={() => setMembersExpanded((value) => !value)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 7,
                width: '100%',
                padding: '10px 0',
                border: 0,
                borderBottom: `1px solid ${C.border}`,
                background: 'transparent',
                color: C.textSecondary,
                cursor: 'pointer',
                textAlign: 'left',
                fontSize: 12,
              }}
            >
              <span style={{ width: 16, color: C.primary, fontSize: 15, lineHeight: 1 }}>
                {membersExpanded ? '⌄' : '›'}
              </span>
              <span style={{ fontWeight: 550 }}>
                {memberLabel}（{memberCount}）
              </span>
            </button>
            {membersExpanded && (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                  padding: '10px 0 12px',
                  borderBottom: `1px solid ${C.border}`,
                }}
              >
                {sortedMembers.length ? (
                  sortedMembers.map((participant) => (
                    <div
                      key={participant.actor_id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        padding: '6px 7px',
                        borderRadius: 7,
                        background: C.surfaceAlt,
                      }}
                    >
                      <div
                        style={{
                          width: 26,
                          height: 26,
                          borderRadius: '50%',
                          background: participant.actor_kind === 'bot' ? C.primary : C.surface,
                          color: participant.actor_kind === 'bot' ? '#fff' : C.textPrimary,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          flexShrink: 0,
                          fontSize: 10,
                          fontWeight: 650,
                        }}
                      >
                        {participant.name.slice(0, 1)}
                      </div>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div
                          style={{
                            color: C.textPrimary,
                            fontSize: 11,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {participant.name || participant.actor_id}
                        </div>
                        <div style={{ marginTop: 2, color: C.textMuted, fontSize: 9 }}>
                          {ROLE_LABELS[participant.role] ?? participant.role} ·{' '}
                          {participant.actor_kind === 'bot' ? 'Bot' : '用户'}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div style={{ color: C.textMuted, fontSize: 11 }}>暂无成员数据</div>
                )}
              </div>
            )}

            <SectionCard title={`${messageLabel}${noMessagePermission ? '' : `（${msgCount}）`}`} marginTop={12}>
              {noMessagePermission ? (
                <div
                  style={{
                    padding: '20px 8px',
                    color: C.textMuted,
                    fontSize: 11,
                    textAlign: 'center',
                    lineHeight: 1.6,
                  }}
                >
                  {isGroup ? (
                    <>
                      当前用户在该协作群中没有可用的本人身份，
                      <br />
                      无群消息查询权限。
                    </>
                  ) : (
                    <>
                      该节点的执行 Bot 属于其他用户，
                      <br />
                      无对话消息查看权限。
                    </>
                  )}
                </div>
              ) : msgCount > 0 ? (
                messages.map((message) => <ChatBubble key={message.id} msg={message} />)
              ) : (
                <Empty description="暂无对话消息" minHeight={80} />
              )}
            </SectionCard>
          </>
        )}
      </div>
    </div>
  );
};

export const GroupDrillDownPanel: React.FC<{
  nodes: TaskNodeView[];
  activeNodeId: string;
  bcsBaseUrl: string;
  apiBaseUrl: string;
  userId?: string;
  onSelect: (nodeId: string) => void;
  onClose: (nodeId: string) => void;
}> = ({ nodes, activeNodeId, bcsBaseUrl, apiBaseUrl, userId, onSelect, onClose }) => {
  const [hoveredTabId, setHoveredTabId] = useState<string | null>(null);
  const activeNode = nodes.find((item) => item.id === activeNodeId) ?? nodes[0];
  if (!activeNode) return <Empty description="暂无协作群执行详情" minHeight={180} />;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        background: C.surface,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 4,
          minHeight: 48,
          padding: '6px 8px',
          borderBottom: `1px solid ${C.border}`,
          background: C.surfaceRaised,
          overflowX: 'auto',
          flexShrink: 0,
        }}
      >
        {nodes.map((node) => {
          const active = node.id === activeNode.id;
          return (
            <div
              key={node.id}
              role="tab"
              aria-selected={active}
              tabIndex={0}
              onClick={() => onSelect(node.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect(node.id);
                }
              }}
              style={{
                position: 'relative',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 190,
                minWidth: 190,
                maxWidth: 190,
                minHeight: 32,
                padding: '5px 28px',
                border: 0,
                borderRadius: 0,
                background: 'transparent',
                color: active ? C.primary : C.textSecondary,
                cursor: 'pointer',
                fontSize: 11,
                transition: 'color 150ms ease-out',
                flexShrink: 0,
                boxSizing: 'border-box',
              }}
            >
              <button
                type="button"
                aria-label={`关闭 ${node.name}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onClose(node.id);
                }}
                onMouseEnter={() => setHoveredTabId(node.id)}
                onMouseLeave={() => setHoveredTabId(null)}
                style={{
                  position: 'absolute',
                  left: 4,
                  top: '50%',
                  transform: 'translateY(-50%)',
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 20,
                  height: 20,
                  padding: 0,
                  border: 0,
                  borderRadius: 5,
                  background: 'transparent',
                  color: active ? C.primary : C.textSecondary,
                  cursor: 'pointer',
                  flexShrink: 0,
                }}
              >
                {hoveredTabId === node.id ? <Close size={14} /> : <Users size={15} />}
              </button>
              <TruncatedText
                value={node.name}
                maxLength={20}
                as="div"
                style={{
                  display: 'block',
                  width: '100%',
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  textAlign: 'center',
                }}
              />
            </div>
          );
        })}
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <GroupSessionView
          node={activeNode}
          bcsBaseUrl={bcsBaseUrl}
          apiBaseUrl={apiBaseUrl}
          userId={userId}
          onBack={() => onClose(activeNode.id)}
        />
      </div>
    </div>
  );
};

// 兼容旧引用
export const GroupDrillDown = GroupSessionView;
