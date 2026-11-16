import { useCallback, useEffect, useRef, useState } from 'react';
import { observationError, readSubworkflows, type FanoutPage } from '../api/run-subworkflows';

/** Mounted only for an expanded node. Requests and cursors never cross execution identities. */
export function useSubworkflows(flowId: string, nodeId: string, groupExecutionId: string) {
  const [data, setData] = useState<FanoutPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [paging, setPaging] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const current = useRef<FanoutPage | null>(null);
  const lifetime = useRef<AbortController | null>(null);
  const watermark = useRef({ identity: '', revision: -1 });

  useEffect(() => {
    const controller = new AbortController(); lifetime.current = controller;
    const identity = JSON.stringify([flowId, nodeId, groupExecutionId]);
    if (watermark.current.identity !== identity) watermark.current = { identity, revision: -1 };
    current.current = null; setData(null); setError(null); setLoading(true); setPaging(false);
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const next = await readSubworkflows(flowId, nodeId, groupExecutionId, controller.signal);
        if (controller.signal.aborted) return;
        if (next.parentFlowId !== flowId || next.groupNodeId !== nodeId || next.groupExecutionId !== groupExecutionId) {
          throw new Error('Snapshot identity mismatch');
        }
        const previous = current.current;
        if (next.revision >= watermark.current.revision) {
          watermark.current.revision = next.revision;
          // Preserve loaded pages only while the server's authorized snapshot is exactly unchanged.
          const merged = previous?.snapshotId === next.snapshotId ? previous : next;
          current.current = merged; setData(merged); setError(null);
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        // Never leave a previously authorized list visible after revocation/read failure.
        current.current = null; setData(null); setError(observationError(error));
      } finally {
        if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(() => void read(), 3000); }
      }
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [flowId, nodeId, groupExecutionId, refresh]);

  const loadMore = useCallback(async () => {
    const previous = current.current; const controller = lifetime.current;
    if (!previous?.nextCursor || !controller || controller.signal.aborted || paging) return;
    setPaging(true);
    try {
      const next = await readSubworkflows(flowId, nodeId, groupExecutionId, controller.signal, previous.nextCursor);
      if (controller.signal.aborted || current.current?.snapshotId !== previous.snapshotId) return;
      if (next.snapshotId !== previous.snapshotId || next.revision !== previous.revision) throw new Error('Snapshot changed');
      const children = [...previous.children, ...next.children];
      const merged = { ...next, children, returnedCount: children.length };
      current.current = merged; setData(merged); setError(null);
    } catch (error) {
      if (!controller.signal.aborted && current.current?.snapshotId === previous.snapshotId) {
        current.current = null; setData(null); setError(observationError(error));
      }
    } finally { if (!controller.signal.aborted) setPaging(false); }
  }, [flowId, nodeId, groupExecutionId, paging]);
  return { data, error, loading, paging, loadMore, retry: () => setRefresh(value => value + 1) };
}
