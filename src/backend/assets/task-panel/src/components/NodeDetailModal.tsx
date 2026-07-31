/**
 * NodeDetailModal — click a node → modal showing TaskNodeDetailView.
 * Data source: GET /api/tasks/{task_id}/nodes/{node_id} (useNodeDetail polls
 * while the node is active). Mirrors StateMachineRunView modal (1490+).
 */
import React from 'react';

import { useNodeDetail } from '../useNodeDetail';
import { getNodeStatusLabel, getNodeStatusTone } from '../utils/statusTone';
import { StatusPill } from '../utils/render';

function Row({ label, value }: { label: string; value: React.ReactNode }): React.ReactElement {
  return (
    <div style={{ display: 'flex', gap: 12, padding: '4px 0', fontSize: 13 }}>
      <div style={{ width: 96, color: '#64748b', flexShrink: 0 }}>{label}</div>
      <div style={{ color: '#1e293b', flex: 1, wordBreak: 'break-word' }}>{value || '—'}</div>
    </div>
  );
}

export function NodeDetailModal({
  taskId,
  nodeId,
  rootPhase,
  onClose,
}: {
  taskId: string;
  nodeId: string;
  rootPhase: string | undefined;
  onClose: () => void;
}): React.ReactElement {
  const { detail, loading } = useNodeDetail(taskId, nodeId, rootPhase);
  const status = detail?.status;
  const tone = getNodeStatusTone(status);

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(15,23,42,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          borderRadius: 10,
          width: 460,
          maxWidth: '90vw',
          maxHeight: '80vh',
          overflow: 'auto',
          padding: 20,
          boxShadow: '0 10px 40px rgba(0,0,0,0.2)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontSize: 15, fontWeight: 600, color: '#0f172a' }}>
            {detail?.display_name || nodeId}
          </div>
          <button
            type="button"
            onClick={onClose}
            style={{ border: 'none', background: 'transparent', fontSize: 18, cursor: 'pointer', color: '#94a3b8' }}
            aria-label="关闭"
          >
            ×
          </button>
        </div>
        <div style={{ marginBottom: 12 }}>
          {loading && !detail ? (
            <span style={{ fontSize: 12, color: '#94a3b8' }}>加载中…</span>
          ) : (
            <StatusPill tone={tone} label={getNodeStatusLabel(status)} />
          )}
        </div>
        {detail ? (
          <>
            <Row label="执行者" value={detail.assignee || '—'} />
            <Row
              label="执行方式"
              value={
                detail.run_mode
                  ? `${detail.run_mode}${detail.collab_mode ? ` · ${detail.collab_mode}` : ''}`
                  : '—'
              }
            />
            <Row
              label="尝试次数"
              value={detail.attempt !== undefined && detail.attempt !== null ? String(detail.attempt) : '—'}
            />
            <Row
              label="验收结果"
              value={
                detail.acceptance_result !== undefined && detail.acceptance_result !== null
                  ? String(detail.acceptance_result)
                  : '—'
              }
            />
            {detail.attempted_executors?.length ? (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>历史执行者</div>
                {detail.attempted_executors.map((a, i) => (
                  <div key={i} style={{ fontSize: 12, color: '#475569', padding: '2px 0' }}>
                    {a.executor_id || '—'}
                    {a.outcome ? ` · ${a.outcome}` : ''}
                    {a.round ? ` · round ${a.round}` : ''}
                  </div>
                ))}
              </div>
            ) : null}
            {detail.artifacts?.length ? (
              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>产物</div>
                {detail.artifacts.map((a, i) => (
                  <div key={i} style={{ fontSize: 12, color: '#475569', padding: '2px 0' }}>
                    {a.name || a.type || 'artifact'}
                    {a.location ? ` · ${a.location}` : ''}
                  </div>
                ))}
              </div>
            ) : null}
            {detail.note ? (
              <div style={{ marginTop: 12, padding: 8, background: '#f8fafc', borderRadius: 6, fontSize: 12, color: '#475569' }}>
                {detail.note}
              </div>
            ) : null}
            {detail.properties?.sub_dag_ref || (detail as any).sub_dag_ref ? (
              <div style={{ marginTop: 12, fontSize: 12, color: '#94a3b8' }}>
                协作子图下钻(v1.5)
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
