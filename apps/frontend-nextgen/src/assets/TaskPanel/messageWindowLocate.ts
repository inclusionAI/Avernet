// @asset-migrated: teamclaw 自研资产
/**
 * 时间窗消息定位(P4,spec §3.2 派生方案):消息无 nodeId 契约,用节点 start_time..end_time
 * 时间窗做"定位到该节点的时间窗"而非精确到最后一条。纯函数 + DOM best-effort:
 * 消息 DOM 命中时间戳属性则滚动,未命中由调用方降级(只跳会话),永不抛错(Ruling 4)。
 */

/** 返回应滚动到的消息 ts:窗口内第一条，保证展示任务/节点开始处；窗口空时退化为起点。 */
export function locateMessageWindowTimestamp(
  messages: Array<{ ts: number }>,
  startTime: number | null,
  endTime: number | null,
): number | null {
  const start = startTime ?? null;
  const end = endTime ?? null;
  if (start === null && end === null) return null;
  if (!messages.length) return null;

  if (start !== null && end !== null) {
    const inWindow = messages.filter((m) => m.ts >= start && m.ts <= end);
    if (inWindow.length) return inWindow[0].ts;
    // 窗口空时优先取起点后的第一条；没有则保留向会话顶部定位的能力。
    const after = messages.filter((m) => m.ts >= start);
    return after.length ? after[0].ts : messages[0]?.ts ?? null;
  }
  const bound = start !== null ? start : end;
  if (bound === null) return null; // 前面已保证二者必有其一,此行为收窄辅助
  const fromBound = messages.filter((m) => m.ts >= bound);
  return fromBound.length ? fromBound[0].ts : messages[0]?.ts ?? null;
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
      const ts = raw !== null ? Number(raw) : NaN;
      if (!Number.isNaN(ts)) candidates.push({ ts, el });
    }
  }
  return candidates;
}

/**
 * 在会话消息 DOM 中定位时间窗并滚动。命中返回 true;无消息 DOM/无窗/无命中返回 false
 * (降级口径:仅完成会话跳转,调用方负责记录)。定位到任务/节点开始处,永不抛错。
 */
export function scrollToMessageTimestampWindow(
  root: ParentNode,
  startTime: number | null,
  endTime: number | null,
): boolean {
  if ((startTime ?? null) === null && (endTime ?? null) === null) return false;
  const candidates = collectMessageCandidates(root);
  if (!candidates.length) return false;

  const targetTs = locateMessageWindowTimestamp(candidates, startTime, endTime);
  if (targetTs === null) return false;

  const hit = candidates.filter((c) => c.ts === targetTs);
  hit[hit.length - 1].el.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  return true;
}