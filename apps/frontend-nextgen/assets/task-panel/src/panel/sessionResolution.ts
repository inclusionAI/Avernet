// @asset-migrated: teamclaw 自研资产
/**
 * 节点 → 会话跳转目标解析(spec §3.2,契约不变决策下的纯前端派生)。
 * 消息无 nodeId 契约,定位精度为"时间窗";后续 task-artifact/graph 改造
 * 提供节点级 session id 时,本模块升级为精确映射即可,调用方不动。
 */
import type { TaskNodeView, TaskView } from './types';

export interface ResolvedSessionTarget {
  sessionId: string;
  /** 会话容器类别:group 需先选群再选会话(宿主 navigator 依赖) */
  kind: 'group' | 'bot' | 'friend' | 'unknown';
  groupId?: string | null;
  botId?: string | null;
  /** 节点执行时间窗(毫秒),消息滚动定位用 */
  startTime?: number | null;
  endTime?: number | null;
}

/** session id 形如 bcs_grp_* 的是协作群会话 */
function isGroupSessionId(sessionId: string): boolean {
  return sessionId.startsWith('bcs_grp_');
}

/**
 * 解析顺序(spec §3.2):
 * ① 节点自带 run_info session(bcs_grp_* → 群;其余按单聊)
 * ② run_mode single_bot → 任务 main_session
 * ③ run_mode coop_group/bbs → derived_session_ids 唯一时精确命中,否则回退 main_session
 * 全链无果 → null(调用方不发起跳转)。
 */
export function resolveNodeSessionTarget(node: TaskNodeView, view: TaskView): ResolvedSessionTarget | null {
  const timeWindow = {
    startTime: node.startedAtMs ?? null,
    endTime: node.endAtMs ?? null,
  };

  // ① 节点自带会话
  if (node.sessionId) {
    const isGroup = node.groupId !== null && node.groupId !== undefined ? true : isGroupSessionId(node.sessionId);
    return {
      sessionId: node.sessionId,
      kind: isGroup ? 'group' : 'bot',
      groupId: node.groupId ?? (isGroup ? view.sourceGroupId ?? null : null),
      botId: isGroup ? null : node.assignee ?? view.ownerBotId ?? null,
      ...timeWindow,
    };
  }

  const runMode = node.runMode ?? null;
  const mainSessionId = view.mainSessionId ?? null;

  // ② 单聊节点 → 主会话
  if (runMode === 'single_bot' && mainSessionId) {
    return {
      sessionId: mainSessionId,
      kind: 'bot',
      groupId: null,
      botId: node.assignee ?? view.ownerBotId ?? null,
      ...timeWindow,
    };
  }

  // ③ 群/bbs 节点:派生会话唯一 → 精确;否则回退主会话
  if (runMode === 'coop_group' || runMode === 'bbs') {
    const derived = view.derivedSessionIds ?? [];
    if (derived.length === 1) {
      return {
        sessionId: derived[0],
        kind: isGroupSessionId(derived[0]) ? 'group' : 'unknown',
        groupId: node.groupId ?? view.sourceGroupId ?? null,
        botId: null,
        ...timeWindow,
      };
    }
    if (mainSessionId) {
      return {
        sessionId: mainSessionId,
        kind: isGroupSessionId(mainSessionId) ? 'group' : 'bot',
        groupId: isGroupSessionId(mainSessionId) ? view.sourceGroupId ?? null : null,
        botId: view.ownerBotId ?? null,
        ...timeWindow,
      };
    }
    return null;
  }

  // 未知 run_mode:有主会话则保守回退,否则放弃
  if (mainSessionId) {
    return {
      sessionId: mainSessionId,
      kind: isGroupSessionId(mainSessionId) ? 'group' : 'bot',
      groupId: isGroupSessionId(mainSessionId) ? view.sourceGroupId ?? null : null,
      botId: view.ownerBotId ?? null,
      ...timeWindow,
    };
  }
  return null;
}