// @asset-migrated: teamclaw 自研资产
/**
 * 时间窗消息定位(P4,spec §3.2 派生方案):消息无 nodeId 契约,用节点 start_time..end_time
 * 时间窗做"定位到该节点的时间窗"而非精确到最后一条。纯函数 + DOM best-effort:
 * 消息 DOM 命中时间戳属性则滚动,未命中由调用方降级(只跳会话),永不抛错(Ruling 4)。
 */

/** 返回应滚动到的消息 ts:窗口内最后一条;窗口空时取 start 之前最近一条;无窗/无果 null。 */
export function locateMessageWindowTimestamp(
  messages: Array<{ ts: number }>,
  startTime: number | null,
  endTime: number | null,
): number | null {
  if (startTime == null && endTime == null) return null;
  if (!messages.length) return null;

  if (startTime != null && endTime != null) {
    const inWindow = messages.filter((m) => m.ts >= startTime && m.ts <= endTime);
    if (inWindow.length) return inWindow[inWindow.length - 1].ts;
    // 窗口空 → 起点之前最近一条(节点开始前的最后上下文)
    const before = messages.filter((m) => m.ts <= startTime);
    return before.length ? before[before.length - 1].ts : null;
  }
  const bound = startTime ?? endTime!;
  const upTo = messages.filter((m) => m.ts <= bound);
  return upTo.length ? upTo[upTo.length - 1].ts : null;
}

/** 消息 DOM 时间戳候选属性(不同 SDK 版本暴露差异,best-effort 全试)。 */
const TS_ATTRS = ['data-ts', 'data-timestamp', 'data-time'];

interface MessageDomCandidate {
  ts: number;
  el: Element;
}

function collectMessageCandidates(root: ParentNode): MessageDomCandidate[] {
  const candidates: MessageDomCandidate[] = [];
  for (const attr of TS_ATTRS) {
    for (const el of Array.from(root.querySelectorAll(`[${attr}]`))) {
      const raw = el.getAttribute(attr);
      const ts = raw != null ? Number(raw) : NaN;
      if (!Number.isNaN(ts)) candidates.push({ ts, el });
    }
  }
  return candidates;
}

/**
 * 在会话消息 DOM 中定位时间窗并滚动。命中返回 true;无消息 DOM/无窗/无命中返回 false
 * (降级口径:仅完成会话跳转,调用方负责记录)。只滚动一次,永不抛错。
 */
export function scrollToMessageTimestampWindow(
  root: ParentNode,
  startTime: number | null,
  endTime: number | null,
): boolean {
  if (startTime == null && endTime == null) return false;
  const candidates = collectMessageCandidates(root);
  if (!candidates.length) return false;

  const targetTs = locateMessageWindowTimestamp(candidates, startTime, endTime);
  if (targetTs == null) return false;

  const hit = candidates.filter((c) => c.ts === targetTs);
  hit[hit.length - 1].el.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  return hit.length > 0;
}