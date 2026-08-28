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
    timestamp: typeof ts === 'number' ? ts : ts ? new Date(ts as string).getTime() : Date.now(),
  };
}

/** 拉取会话消息。
 * - 群执行（groupId 非空）：优先走预发 OpenAPI /openapi/v1/collaboration/sessions/{sid}/messages；传入 bcsBaseUrl 时兼容 BCS raw
 * - 单 bot 执行（groupId 空）：/openapi/v1/bots/{botId}/sessions/{sid}/messages（需 user_id）
 * session_id 格式：群=bcs_grp_xxx:xxx，单聊=session:uuid:user:xxx，后端均按完整透传串匹配。
 */
async function fetchSessionMessages(
  sessionId: string,
  bcsBaseUrl: string,
  isGroup: boolean,
  botId?: string | null,
  userId?: string,
): Promise<GroupMessage[]> {
  if (isMockId(sessionId)) return [];
  let url: string;
  let groupRaw = false;
  if (isGroup && bcsBaseUrl) {
    // 群 session → BCS raw
    url = `${bcsBaseUrl}/sessions/${encodeURIComponent(sessionId)}/messages`;
    groupRaw = true;
  } else if (isGroup) {
    url = `/openapi/v1/collaboration/sessions/${encodeURIComponent(sessionId)}/messages`;
  } else {
    // 单聊 session → openapi bots sessions messages（需 botId）
    if (!botId) return [];
    const query = new URLSearchParams({ page: '1', page_size: '100' });
    if (userId) query.set('user_id', userId);
    url = `/openapi/v1/bots/${encodeURIComponent(botId)}/sessions/${encodeURIComponent(sessionId)}/messages?${query}`;
  }
  const resp = await fetch(url, { credentials: 'include' });
  if (!resp.ok) throw new Error(`会话消息请求失败（${resp.status}）`);
  const json = await resp.json();
  const raw = groupRaw ? json : json?.data ?? json;
  // 单聊接口返回 { items: [{message_id, role, content, gmt_create, ...}] }
  const list = Array.isArray(raw) ? raw : raw?.items ?? [];
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

export interface GroupMessage {
  sender: string;
  content: string;
  message_type: string;
  bot_name?: string;
  timestamp: number | string;
  id: string;
}

/** 群聊消息流中的系统事件、system Bot 回复和注入给 Bot 的 GroupContext 不是用户可读消息。 */
export function isNonMessageGroupContent(
  message: Pick<GroupMessage, 'message_type' | 'content' | 'sender' | 'bot_name'>,
): boolean {
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

const ChatBubble: React.FC<{ msg: GroupMessage }> = ({ msg }) => {
  const sender = msg.bot_name ?? msg.sender ?? '未知成员';
  const isHuman = msg.message_type.toLowerCase() === 'human';
  const kindLabel = isHuman ? '用户' : 'Bot';

  // 过滤函数已经在入状态前执行，这里保留防御性判断，避免异常数据漏出。
  if (isNonMessageGroupContent(msg)) return null;

  const avatarColor = isHuman ? C.surfaceAlt : senderColor(sender);
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
        <div
          style={{
            padding: '9px 11px',
            border: `1px solid ${isHuman ? C.border : `${avatarColor}28`}`,
            borderRadius: '4px 10px 10px 10px',
            background: isHuman ? C.surfaceRaised : C.surface,
            boxShadow: '0 2px 8px rgba(29, 33, 41, 0.04)',
            color: C.textPrimary,
            fontSize: 12,
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            overflowWrap: 'anywhere',
          }}
        >
          {msg.content || '（空消息）'}
        </div>
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [group, setGroup] = useState<GroupData | null>(null);
  const [messages, setMessages] = useState<GroupMessage[]>([]);
  const [membersExpanded, setMembersExpanded] = useState(false);

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
    // group_id 非空（群执行）→ 查群成员（预发 OpenAPI；传入 bcsBaseUrl 时兼容 BCS raw）
    // group_id 空（单 bot）→ bot 信息由后端 dashboard 返回（待实现），本地用 node executor 兜底
    const memberPromise = groupId
      ? fetchGroupDetail(groupId, bcsBaseUrl)
      : Promise.resolve(
          assignee
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
    Promise.all([memberPromise, fetchSessionMessages(sessionId, bcsBaseUrl, Boolean(groupId), assignee, userId)])
      .then(([g, msgs]) => {
        if (cancelled) return;
        setGroup(g);
        setMessages(filterGroupMessages(msgs as GroupMessage[]));
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : '加载执行会话信息失败');
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [groupId, sessionId, bcsBaseUrl, apiBaseUrl, assignee, userId]);

  const isGroup = Boolean(groupId);
  const memberCount = group?.participants.length ?? 0;
  const msgCount = messages.length;
  const groupName = node.name || group?.name || (isGroup ? '协作群执行详情' : '执行会话详情');
  const masterBot = resolveMasterBot(group?.participants ?? [], isGroup ? null : node.executor);
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
                {group?.participants.length ? (
                  group.participants.map((participant) => (
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

            {isGroup && (
              <SectionCard title={`${messageLabel}（${msgCount}）`} marginTop={12}>
                {msgCount > 0 ? (
                  messages.map((message) => <ChatBubble key={message.id} msg={message} />)
                ) : (
                  <Empty description="暂无对话消息" minHeight={80} />
                )}
              </SectionCard>
            )}
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
