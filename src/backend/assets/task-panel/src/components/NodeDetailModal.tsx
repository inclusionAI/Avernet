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

function Section({ title, children }: { title: string; children: React.ReactNode }): React.ReactElement {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 13, color: '#1e293b', wordBreak: 'break-word' }}>{children}</div>
    </div>
  );
}

/**
 * 按 phase_label 渲染规划节点的真实内容(spawn_build_dag 挂在 node.properties 里):
 * 任务识别→任务明细、任务明确→任务Spec 五要素、确认开始执行→执行计划摘要。
 * 无 phase_label(DIPATCH 叶子等)→ 返回 null,由执行类字段接管。*/
function NodeContent({ properties }: { properties: Record<string, unknown> | undefined }): React.ReactElement | null {
  if (!properties) return null;
  const phaseLabel = properties.phase_label as string | undefined;
  if (!phaseLabel) return null;

  if (phaseLabel === '任务识别') {
    const tags = (properties.tags as string[] | undefined) ?? [];
    return (
      <>
        <Section title="任务标题">{String(properties.task_title ?? '—')}</Section>
        <Section title="任务摘要">{String(properties.task_summary ?? '—')}</Section>
        {tags.length ? <Section title="标签">{tags.join(', ')}</Section> : null}
      </>
    );
  }
  if (phaseLabel === '任务明确') {
    const ts = (properties.task_spec as Record<string, unknown> | undefined) ?? {};
    const deliverables = (ts.deliverables as Array<Record<string, string>> | undefined) ?? [];
    const acceptances = (ts.acceptances as Array<Record<string, unknown>> | undefined) ?? [];
    const constraints = (ts.constraints as Array<Record<string, string>> | undefined) ?? [];
    return (
      <>
        <Section title="目标">{String(ts.objective ?? '—')}</Section>
        <Section title="背景">{String(ts.background ?? '—')}</Section>
        {deliverables.length ? (
          <Section title="交付物">
            {deliverables.map((d, i) => (
              <div key={i}>{d.type}{d.location ? ` · ${d.location}` : ''}</div>
            ))}
          </Section>
        ) : null}
        {acceptances.length ? (
          <Section title="验收标准">
            {acceptances.map((a, i) => {
              const desc = (a.properties as Record<string, unknown> | undefined)?.description;
              return <div key={i}>{String(a.kind ?? '')}{desc ? ` · ${desc}` : ''}</div>;
            })}
          </Section>
        ) : null}
        {constraints.length ? (
          <Section title="约束">
            {constraints.map((c, i) => (
              <div key={i}>{c.kind} · {c.text}</div>
            ))}
          </Section>
        ) : null}
      </>
    );
  }
  if (phaseLabel === '确认开始执行') {
    const ps = (properties.plan_summary as Record<string, unknown> | undefined) ?? {};
    const subs = (ps.sub_tasks as Array<Record<string, string>> | undefined) ?? [];
    return (
      <>
        <Section title="子任务数">{String(ps.sub_task_count ?? '—')}</Section>
        <Section title="置信度">{String(ps.confidence ?? '—')}</Section>
        {subs.length ? (
          <Section title="子任务">
            {subs.map((s, i) => (
              <div key={i}>{s.node_id} · {s.spec}</div>
            ))}
          </Section>
        ) : null}
      </>
    );
  }
  return null;
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
  // 规划节点(任务识别/任务明确/确认开始执行)有 phase_label → 显示节点内容,
  // 隐藏执行者/执行方式/尝试次数/验收等执行类字段(控制节点从不执行,显示「—」误导)。
  const isControlNode = !!detail?.properties?.phase_label;

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
            <NodeContent properties={detail.properties} />
            {!isControlNode ? (
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
              </>
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
