import { useEffect, useMemo, useState, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { api, type EvolveTaskLogArchive } from '@avernet/clawweb-shared/web/api/client';
import { monitoringErrorText } from '../../../../api/monitoring';
import { sessionRecoveryApi } from '../../../../api/session-recovery';
import type { SessionRecoveryTask } from '../../../../types/session-recovery';
import { MonitoringIcon } from '../MonitoringIcon';
import './session-recovery.css';

const statusLabels: Record<SessionRecoveryTask['status'], string> = {
  running: '执行中',
  waiting_owner: '等待方案确认',
  waiting_effect: '等待效果',
  completed: '已完成',
  failed: '执行失败',
};

const effectLabels: Record<NonNullable<SessionRecoveryTask['judgment']>['effect'], string> = {
  recovered: '已恢复',
  not_recovered: '未恢复',
  unknown: '尚未确认',
  not_intervened: '未实际干预',
};

const actorLabels: Record<SessionRecoveryTask['timeline'][number]['actor'], string> = {
  service: '自愈服务',
  agent: '自愈 Agent',
  owner: 'Owner',
};

const levelLabels: Record<SessionRecoveryTask['level'], string> = {
  debug: 'Debug',
  approval: '方案审批',
  automatic: '全自动',
};

const phases = [
  { key: 'assess', title: '判断干预方式', description: '确认原问题和处理路径' },
  { key: 'observe', title: '投递恢复消息', description: '推动原会话继续处理' },
  { key: 'plan', title: '形成修复方案', description: '查看方案、配合事项并确认' },
  { key: 'apply', title: '执行环境修复', description: '通过受控服务落地方案' },
  { key: 'verify', title: '验证恢复效果', description: '核对结果并保留证据' },
] as const;

type RecoveryPhase = SessionRecoveryTask['phase'];
type RecoveryEvent = SessionRecoveryTask['timeline'][number];
const actionStatusLabels: Record<string, string> = {
  pending: '等待执行', running: '执行中', executing: '执行中', succeeded: '成功', failed: '失败', unknown: '结果未知', canceled: '已取消',
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function groupPhaseEvents(task: SessionRecoveryTask | null): Record<RecoveryPhase, RecoveryEvent[]> {
  const grouped: Record<RecoveryPhase, RecoveryEvent[]> = { assess: [], observe: [], plan: [], apply: [], verify: [] };
  if (!task) return grouped;
  let active: RecoveryPhase = 'assess';
  for (const event of [...task.timeline].sort((a, b) => a.at_ms - b.at_ms)) {
    const explicit = event.payload.phase;
    let phase: RecoveryPhase = active;
    if (typeof explicit === 'string' && phases.some(item => item.key === explicit)) phase = explicit as RecoveryPhase;
    else if (event.kind === 'assessment') phase = 'assess';
    else if (event.kind === 'plan') phase = 'plan';
    else if (event.kind === 'owner_decision' || event.kind === 'notification' && event.payload.kind === 'plan') phase = 'plan';
    else if (event.kind === 'message' || record(event.payload.result).initial_message === true) phase = active === 'verify' ? 'verify' : 'observe';
    else if (event.kind === 'operation' && typeof event.payload.success === 'boolean') phase = 'apply';
    grouped[phase].push(event);
    active = phase;
    // Business events delimit the next stage; tool calls retain their chronological order.
    if (event.kind === 'assessment') active = event.payload.decision === 'environment' ? 'plan' : 'observe';
    else if (event.kind === 'plan') active = task.level === 'automatic' && task.plan?.owner_cooperation.length === 0 ? 'apply' : 'plan';
    else if (event.kind === 'owner_decision' && event.payload.approved === true) active = 'apply';
    else if (event.kind === 'operation' && event.payload.success === true) active = 'verify';
  }
  return grouped;
}

const time = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  dateStyle: 'short',
  timeStyle: 'medium',
});

function formatTime(value?: number | null): string {
  return typeof value === 'number' ? time.format(value) : '—';
}

function safeJson(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function StatusPill({ status, children }: { status: string; children: ReactNode }) {
  const tone = status === 'failed' || status === 'not_recovered'
    ? 'danger'
    : status === 'completed' || status === 'succeeded' || status === 'recovered'
      ? 'success'
      : status === 'waiting_owner' || status === 'waiting_effect' || status === 'unknown'
        ? 'warning'
        : status === 'running' || status === 'executing'
          ? 'running'
          : 'neutral';
  return <span className={`sr-pill ${tone}`}>{children}</span>;
}

function DetailRow({ label, children, mono = false }: { label: string; children: ReactNode; mono?: boolean }) {
  return <div className="sr-detail-row"><dt>{label}</dt><dd className={mono ? 'mono' : ''}>{children || '—'}</dd></div>;
}

function RawDetails({ value }: { value: unknown }) {
  return <pre className="sr-raw">{safeJson(value)}</pre>;
}

function CardHeader({ title, description, right }: { title: string; description?: string; right?: ReactNode }) {
  return <div className="sr-card-header">
    <div>
      <h2>{title}</h2>
      {description && <p>{description}</p>}
    </div>
    {right}
  </div>;
}

function AgentSessionLogs({ task }: { task: SessionRecoveryTask }) {
  const [archives, setArchives] = useState<EvolveTaskLogArchive[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [downloading, setDownloading] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void api.evolve.listTaskLogArchives(task.taskId).then(value => {
      if (!Array.isArray(value.items)) throw new Error('日志归档响应不完整，请刷新重试。');
      if (active) {
        setArchives(value.items.filter(item => item.status === 'succeeded' && item.metadata?.source === 'ais'));
        setError('');
      }
    }).catch(failure => { if (active) setError(failure?.status === 403 ? '只有任务 Owner 或管理员可下载 Agent 会话日志。' : monitoringErrorText(failure)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [task]);

  const download = async (archiveId: string) => {
    setDownloading(archiveId);
    setError('');
    try {
      const result = await api.evolve.getTaskLogArchiveDownloadUrl(task.taskId, archiveId);
      const anchor = document.createElement('a');
      anchor.href = result.url; anchor.download = result.filename;
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
    } catch (failure) { setError(monitoringErrorText(failure)); }
    finally { setDownloading(null); }
  };
  return <section className="sr-card compact">
    <CardHeader title="Agent 会话日志" description="下载自愈 Agent 的原始会话归档，供排查执行过程。" />
    {error && <p role="alert">{error}</p>}
    {!archives.length && !error && <p className="sr-empty">{loading ? '正在读取日志归档…' : '暂无可下载归档，执行结束并上传后可下载。'}</p>}
    {archives.map(archive => {
      const completedAt = archive.completedAt == null ? NaN : typeof archive.completedAt === 'number'
        ? archive.completedAt < 10_000_000_000 ? archive.completedAt * 1000 : archive.completedAt
        : new Date(archive.completedAt).getTime();
      return <div key={archive.archiveId} className="sr-note-box">
      <p>执行批次：{String(archive.metadata?.stepId ?? archive.archiveId).replace(`${task.taskId}-`, '')}</p>
      <p>{formatTime(Number.isFinite(completedAt) ? completedAt : undefined)} · {Math.ceil((archive.artifact?.size ?? 0) / 1024)} KB</p>
      <button type="button" className="sr-button" disabled={downloading !== null}
        aria-label={`下载 Agent 会话日志 · ${archive.metadata?.stepId ?? archive.archiveId}`}
        onClick={() => void download(archive.archiveId)}>{downloading === archive.archiveId ? '准备下载…' : '下载 Agent 会话日志'}</button>
    </div>;
    })}
  </section>;
}

function PlanMarkdown({ text }: { text: string }) {
  // Plain legacy prose gets paragraph breaks at existing sentence boundaries only.
  // Authored Markdown and multiline content retain their original structure.
  const markdown = /[\n`*#\[\]<>~]/.test(text) ? text : text.replace(/([。；])(?=\S)/g, '$1\n\n');
  return <div className="sr-plan-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: ({ node, ...props }) => { void node; return <a {...props} target="_blank" rel="noreferrer" />; } }}>{markdown}</ReactMarkdown></div>;
}

function RecoveryWorkflow({ task, currentPhase, selected, onSelect, grouped, rawEvidence, loadEvidence, children }: {
  task: SessionRecoveryTask;
  currentPhase: RecoveryPhase;
  selected: RecoveryPhase;
  onSelect: (phase: RecoveryPhase) => void;
  grouped: Record<RecoveryPhase, RecoveryEvent[]>;
  rawEvidence: Record<string, unknown>;
  loadEvidence: (id: string) => void;
  children?: ReactNode;
}) {
  const selectedPhase = phases.find(phase => phase.key === selected)!;
  const events = grouped[selected];
  return <section className="sr-card sr-workflow-card">
    <CardHeader title="自愈流程" description="自愈 Agent 判断处理方式，按需投递恢复消息、生成方案、执行修复并验证效果。" right={<StatusPill status={task.status}>{statusLabels[task.status]}</StatusPill>} />
    <div className="sr-workflow" role="tablist" aria-label="自愈流程阶段">
      {phases.map((phase, index) => {
        const current = currentPhase === phase.key;
        const visited = grouped[phase.key].length > 0;
        const failed = current && task.status === 'failed';
        const className = ['sr-workflow-step', selected === phase.key ? 'selected' : '', current ? 'current' : '', failed ? 'failed' : ''].filter(Boolean).join(' ');
        return <button type="button" role="tab" key={phase.key} id={`recovery-phase-${phase.key}`} className={className} aria-controls="recovery-stage-process" aria-selected={selected === phase.key} aria-current={current ? 'step' : undefined} tabIndex={selected === phase.key ? 0 : -1} onClick={() => onSelect(phase.key)} onKeyDown={(event) => {
          const offset = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
          if (!offset && event.key !== 'Home' && event.key !== 'End') return;
          event.preventDefault();
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? phases.length - 1 : (index + offset + phases.length) % phases.length;
          onSelect(phases[next].key);
          (event.currentTarget.parentElement?.children[next] as HTMLElement)?.focus();
        }}>
          <div className="sr-step-top"><span className="sr-step-index">{index + 1}</span><span className="sr-step-state">{current ? statusLabels[task.status] : visited ? '有记录' : '未进入'}</span></div>
          <span className="sr-step-title">{phase.title}</span>
          <span className="sr-step-description">{phase.description}</span>
          <span className="sr-step-count">{visited ? `${grouped[phase.key].length} 条记录 · 点击查看` : '点击查看阶段记录'}</span>
        </button>;
      })}
    </div>
    <div id="recovery-stage-process" className="sr-stage-process" role="tabpanel" aria-labelledby={`recovery-phase-${selected}`}>
      {children}
      <CardHeader title={`${selectedPhase.title} · 执行过程`} description="逐条查看 Agent 的动作、结果和判断，操作细节可展开。" right={<span className="sr-count">{events.length} 条记录</span>} />
      {events.length ? <ol className="sr-process-list">{events.map((event, index) => <ProcessAction key={event.event_id} task={task} event={event} index={index} rawEvidence={rawEvidence} loadEvidence={loadEvidence} onViewAction={(actionId) => {
        onSelect('plan');
        window.requestAnimationFrame(() => document.getElementById(`recovery-action-${encodeURIComponent(actionId)}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
      }} />)}</ol> : <div className="sr-empty">{selected === currentPhase ? '当前阶段尚未收到动作记录。' : '此阶段暂无动作记录。'}</div>}
    </div>
  </section>;
}

const operationTitles: Record<string, string> = {
  session_read: '读取原会话记录', trajectory_read: '读取原任务执行轨迹',
  runtime_inspect: '检查 Bot 运行环境', engine_read: '读取 Bot 配置',
  logs_search: '查询故障日志', retry_message: '向原会话发送恢复消息',
  apply_action: '执行方案中的修复动作', reconcile_write: '核实操作结果',
};
const eventTitles: Record<string, string> = {
  created: '接收问题诊断', assessment: '判断干预方式', observation: '检查原任务恢复情况',
  plan: '提交修复方案', notification: '发送通知', owner_decision: '记录 Owner 的方案决定',
};

function actionTitle(event: RecoveryEvent, request: Record<string, unknown>, source?: string): string {
  if (event.kind === 'observation' && typeof event.payload.sourceToolCallId === 'string') return '记录工具调用结论';
  if (eventTitles[event.kind]) return eventTitles[event.kind];
  // Like Repair, prefer the action's purpose. Detailed reasoning is body text, not a heading.
  if (typeof request.purpose === 'string' && request.purpose.trim()) return request.purpose;
  // Older events may contain a whole analysis instead of an operation purpose.
  if (event.summary.length <= 32 && !event.summary.includes('\n')) return event.summary;
  return operationTitles[String(request.operation)] ?? operationTitles[source ?? ''] ?? '查看本次操作记录';
}

function eventEvidenceIds(event: RecoveryEvent): unknown[] {
  const judgment = record(event.payload.judgment);
  return [
    ...(Array.isArray(judgment.evidence_ids) ? judgment.evidence_ids : []),
    ...(Array.isArray(event.payload.evidence_ids) ? event.payload.evidence_ids : []),
    ...(Array.isArray(event.payload.evidence) ? event.payload.evidence.map(item => record(item).evidence_id) : []),
    ...(Array.isArray(event.payload.evidenceToolCallIds) ? event.payload.evidenceToolCallIds : []),
    event.payload.sourceToolCallId,
    event.payload.evidence_id,
  ];
}

function ProcessAction({ task, event, index, rawEvidence, loadEvidence, onViewAction }: { task: SessionRecoveryTask; event: RecoveryEvent; index: number; rawEvidence: Record<string, unknown>; loadEvidence: (id: string) => void; onViewAction: (id: string) => void }) {
  if (['plan', 'notification', 'owner_decision', 'created', 'execution', 'waiting', 'resumed', 'result', 'annotation'].includes(event.kind)) {
    const submitted = record(event.payload.plan);
    const counts = [Array.isArray(submitted.actions) ? `Agent 操作 ${submitted.actions.length} 项` : '', Array.isArray(submitted.owner_cooperation) ? `Owner 事项 ${submitted.owner_cooperation.length} 项` : ''].filter(Boolean).join(' · ');
    const outcome = event.kind === 'plan' ? `方案已提交${counts ? ` · ${counts}` : ''}`
      : event.kind === 'notification' ? event.payload.sent === true ? '钉钉通知已发送' : event.payload.sent === false ? '钉钉通知发送失败' : event.summary
      : event.kind === 'owner_decision' ? event.payload.approved === true ? 'Owner 已确认完整方案' : event.payload.approved === false ? 'Owner 已拒绝方案' : event.summary
      : event.summary;
    const evidenceIds = eventEvidenceIds(event);
    const relatedEvidence = task.evidence.filter(item => evidenceIds.includes(item.evidence_id) || evidenceIds.includes(item.tool_call_id)
      || event === task.timeline[0] && !task.timeline.some(entry => entry.payload.raw_tool_call_id === item.tool_call_id || eventEvidenceIds(entry).includes(item.evidence_id)));
    return <li className="sr-process-event"><article>
      <div className="sr-process-event-header"><h3>{eventTitles[event.kind] || outcome}</h3><time>{formatTime(event.at_ms)}</time></div>
      {(eventTitles[event.kind] && outcome !== eventTitles[event.kind]) && <p>{outcome}</p>}
      {typeof event.payload.feedback === 'string' && event.payload.feedback.trim() && <PlanMarkdown text={event.payload.feedback} />}
      {event.kind === 'annotation' && typeof event.payload.reason === 'string' && <PlanMarkdown text={event.payload.reason} />}
      <details className="sr-disclosure"><summary>查看记录原文</summary><PlanMarkdown text={event.summary} />
        {relatedEvidence.map(item => <div key={item.evidence_id} className="sr-evidence-card">
          <div className="sr-evidence-excerpt">{item.excerpt}</div>
          <details className="sr-disclosure" onToggle={(toggleEvent) => { if (toggleEvent.currentTarget.open && !rawEvidence[item.tool_call_id]) loadEvidence(item.tool_call_id); }}><summary>查看完整操作回执</summary><RawDetails value={rawEvidence[item.tool_call_id] ?? '正在读取…'} /></details>
        </div>)}
        <RawDetails value={event.payload} />
      </details>
    </article></li>;
  }
  const callId = typeof event.payload.raw_tool_call_id === 'string' ? event.payload.raw_tool_call_id : undefined;
  const evidence = task.evidence.find(item => callId && item.tool_call_id === callId);
  let savedResult: Record<string, unknown> = {};
  if (evidence) {
    try { savedResult = record(JSON.parse(evidence.excerpt)); } catch { /* Evidence may contain text or a truncated receipt. */ }
  }
  const receipt = record(callId ? rawEvidence[callId] : undefined);
  const request = record(receipt.request ?? event.payload.request);
  const result = { ...savedResult, ...record(event.payload.result), ...record(receipt.result) };
  const judgment = record(event.payload.judgment);
  const status = typeof receipt.status === 'string' ? receipt.status : typeof event.payload.status === 'string' ? event.payload.status : '';
  const action = task.plan?.actions.find(item => item.actionId === request.actionId);
  const title = actionTitle(event, request, evidence?.source);
  const linkedConclusion = callId ? task.timeline.find(item => item.kind === 'observation' && item.payload.sourceToolCallId === callId && typeof item.payload.conclusionZh === 'string') : undefined;
  const conclusion = linkedConclusion?.payload ?? (typeof event.payload.sourceToolCallId === 'string' ? event.payload : {});
  const detailText = conclusion.conclusionZh ?? judgment.summary
    ?? (event.summary !== title && event.summary !== request.purpose ? event.summary : undefined);
  const recordedResult = result.summary;
  const resultSummary = typeof recordedResult === 'string' ? recordedResult : ({
    pending: '操作尚未开始。', running: '正在执行，等待返回结果。', executing: '正在执行，等待返回结果。',
    succeeded: '调用已完成。', failed: '调用失败，展开查看原因。', unknown: '调用结果尚未确定。', canceled: '调用已取消。',
  }[status]);
  const actualCommand = typeof request.command === 'string' ? request.command : undefined;
  const actualRequest = request.engineOperation ?? (typeof record(request.request).method === 'string' && typeof record(request.request).path === 'string' ? request.request : undefined);
  const target = task.request.target;
  const error = typeof result.error === 'string' ? result.error : record(result.error).message;
  const evidenceIds = [...eventEvidenceIds(event), ...(linkedConclusion ? eventEvidenceIds(linkedConclusion) : [])];
  const relatedEvidence = task.evidence.filter(item => callId && item.tool_call_id === callId || evidenceIds.includes(item.evidence_id) || evidenceIds.includes(item.tool_call_id)
    // Retain old, unlinked evidence with the task's initial record rather than dropping it.
    || event === task.timeline[0] && !task.timeline.some(entry => entry.payload.raw_tool_call_id === item.tool_call_id || eventEvidenceIds(entry).includes(item.evidence_id)));
  const effect = typeof judgment.effect === 'string' && judgment.effect in effectLabels ? judgment.effect as keyof typeof effectLabels : undefined;
  return <li className={`sr-process-action ${status} ${event.actor === 'service' ? 'control' : ''}`}>
    <div className="sr-process-index">{index + 1}</div>
    <article>
      <div className="sr-process-header"><h3>{title}</h3><div className="sr-process-status">{status && <StatusPill status={status}>{actionStatusLabels[status] || status}</StatusPill>}{effect && <StatusPill status={effect}>{effectLabels[effect]}</StatusPill>}</div></div>
      {resultSummary && <div className="sr-process-result sr-result-preview"><span>结果：</span><PlanMarkdown text={resultSummary} />{typeof result.httpStatus === 'number' && <span className="sr-receipt-code">HTTP {result.httpStatus}</span>}{typeof result.exitCode === 'number' && <span className="sr-receipt-code">退出码 {result.exitCode}</span>}</div>}
      {typeof conclusion.nextAction === 'string' && <div className="sr-process-next"><span>后续动作：</span><PlanMarkdown text={conclusion.nextAction} /></div>}
      <details className="sr-disclosure sr-operation-details" onToggle={(toggleEvent) => { if (toggleEvent.currentTarget.open && callId && !rawEvidence[callId]) loadEvidence(callId); }}>
        <summary><span className="sr-when-closed">展开详情</span><span className="sr-when-open">收起详情</span></summary>
        <div className="sr-operation-body">
          {(callId || event.kind === 'operation') && <dl className="sr-operation-target">
            <DetailRow label="任务目标">{`Bot ${target.bot_id}${target.engine ? ` · ${target.engine}` : ''}${target.env ? ` · ${target.env}` : ''}`}</DetailRow>
            {typeof result.session_id === 'string' && <DetailRow label="读取会话" mono>{result.session_id}</DetailRow>}
          </dl>}
          {typeof error === 'string' && <p className="sr-process-error"><strong>失败原因：</strong>{error}</p>}
          {typeof detailText === 'string' && <div className="sr-operation-field"><h4>{event.kind === 'assessment' || event.kind === 'observation' && !event.payload.sourceToolCallId ? '判断说明' : conclusion.conclusionZh ? '结论' : '记录说明'}</h4><div className="sr-long-text"><PlanMarkdown text={detailText} /></div></div>}
          {typeof event.payload.feedback === 'string' && event.payload.feedback.trim() && <div className="sr-operation-field"><h4>Owner 补充说明</h4><PlanMarkdown text={event.payload.feedback} /></div>}
          {typeof (result.message ?? request.message) === 'string' && <div className="sr-operation-field"><h4>发送的恢复消息</h4><PlanMarkdown text={String(result.message ?? request.message)} /></div>}
          {actualCommand && <details className="sr-disclosure"><summary>查看本次调用的 Shell 命令</summary><RawDetails value={actualCommand} /></details>}
          {actualRequest != null && <details className="sr-disclosure"><summary>查看本次调用的接口请求</summary><RawDetails value={actualRequest} /></details>}
          {(typeof result.stdout === 'string' || typeof result.stderr === 'string') && <details className="sr-disclosure"><summary>查看命令输出</summary>{typeof result.stdout === 'string' && <><p className="sr-note">标准输出</p><RawDetails value={result.stdout} /></>}{typeof result.stderr === 'string' && <><p className="sr-note">错误输出</p><RawDetails value={result.stderr} /></>}</details>}
          {action && <button type="button" className="sr-action-reference" onClick={() => onViewAction(action.actionId)}>方案动作：{action.summary} →</button>}
          {relatedEvidence.length > 0 && <section className="sr-operation-evidence"><h4>相关记录与依据</h4>{relatedEvidence.map((item, evidenceIndex) => <div key={item.evidence_id} className="sr-evidence-card">
            <p className="sr-evidence-label">{evidenceIndex + 1}. {operationTitles[item.source] ?? item.source}</p>
            {item.excerpt.trim().startsWith('{') || item.excerpt.trim().startsWith('[')
              ? <details className="sr-disclosure"><summary>查看保存的原始记录</summary><RawDetails value={item.excerpt} /></details>
              : <div className="sr-evidence-excerpt">{item.excerpt}</div>}
            {item.tool_call_id !== callId && <details className="sr-disclosure" onToggle={(toggleEvent) => { if (toggleEvent.currentTarget.open && !rawEvidence[item.tool_call_id]) loadEvidence(item.tool_call_id); }}><summary>查看完整操作回执</summary><RawDetails value={rawEvidence[item.tool_call_id] ?? '正在读取…'} /></details>}
            <details className="sr-disclosure"><summary>记录位置与标识</summary><dl><DetailRow label="位置" mono>{item.location}</DetailRow><DetailRow label="证据编号" mono>{item.evidence_id}</DetailRow></dl></details>
          </div>)}</section>}
          <details className="sr-disclosure"><summary>技术信息</summary><RawDetails value={event.payload} />{callId && <RawDetails value={rawEvidence[callId] ?? '正在读取完整操作原文与回执…'} />}</details>
        </div>
      </details>
      <div className="sr-timeline-meta"><span>{actorLabels[event.actor]}</span><time>{formatTime(event.at_ms)}</time></div>
    </article>
  </li>;
}

function RecoveryPlan({ task, busy, ownerFeedback, cooperationDone, onFeedback, onCooperation, onDecide, review }: {
  review?: ReactNode;
  task: SessionRecoveryTask;
  busy: boolean;
  ownerFeedback: string;
  cooperationDone: boolean;
  onFeedback: (value: string) => void;
  onCooperation: (value: boolean) => void;
  onDecide: (approved: boolean) => void;
}) {
  const plan = task.plan;
  if (!plan) return null;
  return <section id="recovery-plan" className="sr-plan">
    <CardHeader title="修复方案" right={<StatusPill status={plan.approvedAt ? 'completed' : 'waiting_owner'}>{plan.approvedAt ? '方案已确认' : task.status === 'waiting_owner' ? '等待确认' : '方案已提交'}</StatusPill>} />
    <section className="sr-plan-block sr-plan-overview" aria-label="问题与处理思路">
      <h3>问题与处理思路</h3>
      <div className="sr-plan-body sr-plan-summary">
      <PlanMarkdown text={plan.summary} />
      <details className="sr-disclosure sr-plan-support"><summary>查看诊断依据与处理范围</summary>
        <div className="sr-operation-field"><h4>问题与证据依据</h4><PlanMarkdown text={plan.basis} /></div>
        <div className="sr-operation-field"><h4>处理范围</h4><PlanMarkdown text={plan.scope} /></div>
      </details>
      </div>
    </section>
    {(plan.owner_cooperation.length > 0 || plan.actions.length > 0) && <section className="sr-plan-block" aria-label="完整执行安排">
      <h3>执行安排</h3>
      <ol className="sr-plan-body sr-plan-steps">
        {plan.owner_cooperation.map((item, index) => <li key={`owner-${index}`} className="sr-plan-step owner" id={index === 0 ? 'recovery-owner-cooperation' : undefined}>
          <div className="sr-plan-step-heading"><span className="sr-step-actor"><MonitoringIcon name="user" />Owner</span><span className="sr-note">方案确认前处理</span></div>
          <div className="sr-owner-request"><PlanMarkdown text={item.request} /></div>
          <details className="sr-disclosure"><summary>查看这项安排的依据</summary><PlanMarkdown text={item.basis} /></details>
          {task.status === 'waiting_owner' && index === plan.owner_cooperation.length - 1 && <div className="sr-owner-feedback">
            <label>补充信息或处理说明<textarea maxLength={4000} value={ownerFeedback} onChange={(event) => onFeedback(event.target.value)} placeholder="填写处理进度或补充信息，供 Agent 继续处理时参考" /></label>
            <label className="sr-check"><input type="checkbox" checked={cooperationDone} onChange={(event) => onCooperation(event.target.checked)} />我已处理方案中的配合事项</label>
          </div>}
        </li>)}
        {plan.actions.map(action => <li key={action.actionId} id={`recovery-action-${encodeURIComponent(action.actionId)}`} className="sr-plan-step agent">
          <div className="sr-plan-step-heading"><span className="sr-step-actor"><MonitoringIcon name="bot" />自愈 Agent</span><span className="sr-note">按确认的方案继续执行</span></div>
          <div className="sr-action-description"><PlanMarkdown text={action.summary} /></div>
          <details className="sr-disclosure sr-action-safeguards"><summary>查看风险、验证与回退</summary>
            <div className="sr-action-grid">
              <div><h4>风险</h4><PlanMarkdown text={action.risk} /></div>
              <div><h4>验证</h4><PlanMarkdown text={action.verification} /></div>
            </div>
            {action.rollback && <div className="sr-action-rollback"><strong>回退：</strong><PlanMarkdown text={action.rollback} /></div>}
          </details>
          <details className="sr-disclosure"><summary>查看操作细节</summary>
            <p className="sr-note mono">操作标识：{action.actionId}</p>
            {action.operation && <RawDetails value={action.operation} />}
            {action.engineOperation && <><p className="sr-note">参考接口请求</p><RawDetails value={action.engineOperation} /></>}
            {action.command && <><p className="sr-note">参考命令</p><RawDetails value={action.command} /></>}
          </details>
        </li>)}
      </ol>
    </section>}
    <section className="sr-plan-block sr-plan-closeout" aria-label="验收与确认">
      <h3>验收与确认</h3>
      <div className="sr-plan-body">
      <PlanMarkdown text={plan.verification} />
      {task.status === 'waiting_owner' && <div className="sr-decision-panel">
        <h4>确认完整方案</h4>
        <p>请核对上述分工、处理安排与验收标准，确认后 Agent 将继续执行。</p>
        {plan.owner_cooperation.length === 0 && <label>补充信息或处理说明<textarea maxLength={4000} value={ownerFeedback} onChange={(event) => onFeedback(event.target.value)} placeholder="填写补充信息" /></label>}
        <div className="sr-actions"><button type="button" className="sr-button primary" disabled={busy || (plan.owner_cooperation.length > 0 && !cooperationDone)} onClick={() => onDecide(true)}>{busy ? '提交中…' : '确认完整方案'}</button><button type="button" className="sr-button" disabled={busy} onClick={() => onDecide(false)}>拒绝方案</button></div>
      </div>}
      {review}
      </div>
    </section>
  </section>;
}

function ReviewSection({ busy, annotationLayer, annotationCorrect, annotationReason, onLayer, onCorrect, onReason, onSubmit }: {
  busy: boolean;
  annotationLayer: 'delivery' | 'response' | 'effect';
  annotationCorrect: string;
  annotationReason: string;
  onLayer: (value: 'delivery' | 'response' | 'effect') => void;
  onCorrect: (value: string) => void;
  onReason: (value: string) => void;
  onSubmit: () => void;
}) {
  return <section className="sr-review sr-terminal-review">
    <details>
      <summary>人工复核</summary>
      <p>人工标注会与 Agent 的原判断一并保留，不覆盖原结论。</p>
      <div className="sr-form-grid">
        <label>判断层次<select value={annotationLayer} onChange={(event) => onLayer(event.target.value as typeof annotationLayer)}><option value="delivery">触达</option><option value="response">响应</option><option value="effect">效果</option></select></label>
        <label>是否正确<select value={annotationCorrect} onChange={(event) => onCorrect(event.target.value)}><option value="true">正确</option><option value="false">错误</option><option value="unknown">无法判断</option></select></label>
      </div>
      <label>理由<textarea value={annotationReason} maxLength={4000} onChange={(event) => onReason(event.target.value)} /></label>
      <button type="button" className="sr-button primary" disabled={busy || !annotationReason.trim()} onClick={onSubmit}>保存标注</button>
    </details>
  </section>;
}

export default function SessionRecoveryDetail({ taskId, onBack }: { taskId: string; onBack: () => void }) {
  const [loaded, setLoaded] = useState<{ taskId: string; task: SessionRecoveryTask } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [annotationLayer, setAnnotationLayer] = useState<'delivery' | 'response' | 'effect'>('effect');
  const [annotationCorrect, setAnnotationCorrect] = useState('true');
  const [annotationReason, setAnnotationReason] = useState('');
  const [rawEvidence, setRawEvidence] = useState<Record<string, unknown>>({});
  const [ownerFeedback, setOwnerFeedback] = useState('');
  const [cooperationDone, setCooperationDone] = useState(false);
  const [phaseSelection, setPhaseSelection] = useState<{ taskId: string; phase: RecoveryPhase } | null>(null);
  const task = loaded?.taskId === taskId ? loaded.task : null;
  const currentPhaseKey = task?.plan && !task.plan.approvedAt && task.phase === 'apply' ? 'plan' : task?.phase ?? 'assess';
  const selectedPhase = phaseSelection?.taskId === taskId ? phaseSelection.phase : currentPhaseKey;
  const grouped = useMemo(() => groupPhaseEvents(task), [task]);

  useEffect(() => {
    let active = true;
    let controller: AbortController | undefined;
    const load = async () => {
      if (document.visibilityState === 'hidden') return;
      controller?.abort();
      controller = new AbortController();
      const { signal } = controller;
      try {
        const value = await sessionRecoveryApi.detail(taskId, signal);
        if (active && !signal.aborted) {
          if (!value || value.taskId !== taskId || !Array.isArray(value.timeline) || !Array.isArray(value.evidence) || !value.request?.target) {
            setError(record(value).buserviceErrorCode === 'USER_NOT_LOGIN' ? '登录状态已失效，请重新登录后刷新。' : '自愈任务详情响应不完整，请刷新重试。');
            return;
          }
          setLoaded({ taskId, task: value });
          setError('');
        }
      } catch (failure) {
        if (active && !signal.aborted) setError(monitoringErrorText(failure));
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 20000);
    const visible = () => { if (document.visibilityState === 'visible') void load(); };
    document.addEventListener('visibilitychange', visible);
    return () => {
      active = false;
      window.clearInterval(timer);
      controller?.abort();
      document.removeEventListener('visibilitychange', visible);
    };
  }, [taskId, revision]);

  useEffect(() => { setCooperationDone(false); setOwnerFeedback(''); }, [taskId, task?.plan?.digest]);
  useEffect(() => { setAnnotationReason(''); setRawEvidence({}); }, [taskId]);

  const decide = async (approved: boolean) => {
    if (!task?.plan || busy) return;
    setBusy(true);
    try {
      await sessionRecoveryApi.decide(taskId, task.plan.digest, approved, cooperationDone, ownerFeedback);
      setRevision((value) => value + 1);
    } catch (failure) {
      setError(monitoringErrorText(failure));
    } finally {
      setBusy(false);
    }
  };

  const loadEvidence = async (id: string) => {
    try {
      const value = await sessionRecoveryApi.toolCall(taskId, id);
      setRawEvidence((previous) => ({ ...previous, [id]: value }));
    } catch (failure) {
      setError(monitoringErrorText(failure));
    }
  };

  const annotate = async () => {
    if (!task || !annotationReason.trim() || busy) return;
    setBusy(true);
    try {
      await sessionRecoveryApi.annotate(taskId, {
        layer: annotationLayer,
        correct: annotationCorrect === 'unknown' ? null : annotationCorrect === 'true',
        reason: annotationReason,
        evidence_ids: [],
      });
      setAnnotationReason('');
      setRevision((value) => value + 1);
    } catch (failure) {
      setError(monitoringErrorText(failure));
    } finally {
      setBusy(false);
    }
  };

  const currentPhase = phases.find((item) => item.key === currentPhaseKey);
  const lastEvent = task?.timeline.at(-1);
  const effect = task?.judgment?.effect ?? 'unknown';
  const review = task && ['completed', 'failed'].includes(task.status) ? <ReviewSection busy={busy} annotationLayer={annotationLayer} annotationCorrect={annotationCorrect} annotationReason={annotationReason} onLayer={setAnnotationLayer} onCorrect={setAnnotationCorrect} onReason={setAnnotationReason} onSubmit={() => void annotate()} /> : null;
  const viewPlan = () => {
    setPhaseSelection({ taskId, phase: 'plan' });
    window.requestAnimationFrame(() => document.getElementById('recovery-plan')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  };

  return <main className="session-recovery-detail">
    <button type="button" onClick={onBack} className="sr-back"><MonitoringIcon name="arrow" />返回监控</button>
    <header className="sr-hero">
      <div>
        <div className="sr-kicker">{task && <StatusPill status={task.status}>{statusLabels[task.status]}</StatusPill>}<span>会话自愈</span><code>{taskId}</code></div>
        <h1>自愈任务详情</h1>
        {task && <div className="sr-target"><span><MonitoringIcon name="bot" /></span><div><strong>{task.request.target.bot_id}</strong><p>{[task.request.target.engine, task.request.target.env, levelLabels[task.level]].filter(Boolean).join(' · ')}</p></div></div>}
      </div>
      <button type="button" className="sr-button" onClick={() => setRevision((value) => value + 1)}><MonitoringIcon name="refresh" />刷新任务</button>
    </header>

    {error && <div role="alert" className="sr-alert danger">
      <div><strong>{error}</strong>{task && <p>本次刷新未完成，当前保留上次成功读取的数据。</p>}</div>
      <button type="button" className="sr-button" onClick={() => setRevision((value) => value + 1)}>重新加载</button>
    </div>}

    {!task ? <div role="status" className="sr-empty large">{error ? '任务未能加载，请重试。' : '正在读取任务…'}</div> : <>
      <section className="sr-summary-grid">
        <article className="sr-summary-card"><span>当前阶段</span><strong>{currentPhase?.title || '—'}</strong><PlanMarkdown text={lastEvent?.summary || '等待任务执行记录。'} /></article>
        <article className="sr-summary-card"><span>恢复效果</span><strong>{effectLabels[effect]}</strong><PlanMarkdown text={task.judgment?.summary || '等待 Agent 提交效果判断和证据。'} /></article>
      </section>

      {task.status === 'failed' && <section role="alert" className="sr-alert danger"><MonitoringIcon name="alert" /><div><strong>自愈执行失败</strong><PlanMarkdown text={task.judgment?.summary || lastEvent?.summary || '本次自愈执行未完成，详见阶段操作与证据。'} /></div></section>}
      {task.status === 'waiting_owner' && <section role="status" className="sr-alert warning"><MonitoringIcon name="user" /><div><strong>需要确认修复方案</strong><p>请查看完整方案及配合事项，确认后 Agent 将继续处理。</p></div><button type="button" className="sr-button primary" onClick={viewPlan}>查看方案</button></section>}

      <div className="sr-layout">
        <div className="sr-main">
          <RecoveryWorkflow task={task} currentPhase={currentPhaseKey} selected={selectedPhase} onSelect={(phase) => setPhaseSelection({ taskId, phase })} grouped={grouped} rawEvidence={rawEvidence} loadEvidence={(id) => void loadEvidence(id)}>
            {selectedPhase === 'plan' && <RecoveryPlan task={task} busy={busy} ownerFeedback={ownerFeedback} cooperationDone={cooperationDone} onFeedback={setOwnerFeedback} onCooperation={setCooperationDone} onDecide={(approved) => void decide(approved)} review={review} />}
            {!task.plan && selectedPhase === currentPhaseKey && review}
            {task.plan && selectedPhase === 'verify' && review && <button type="button" className="sr-button" onClick={viewPlan}>查看方案与人工复核</button>}
          </RecoveryWorkflow>
        </div>
        <aside className="sr-side">
          <AgentSessionLogs key={task.taskId} task={task} />
          <section className="sr-card compact">
            <CardHeader title="任务信息" />
            <dl>
              <DetailRow label="目标 Bot" mono>{task.request.target.bot_id}</DetailRow>
              <DetailRow label="目标环境">{task.request.target.env}</DetailRow>
              <DetailRow label="引擎">{task.request.target.engine}</DetailRow>
              <DetailRow label="执行权限">{levelLabels[task.level]}</DetailRow>
              <DetailRow label="故障标签" mono>{task.request.tc_fault_label}</DetailRow>
              <DetailRow label="创建时间">{formatTime(task.timeline[0]?.at_ms)}</DetailRow>
              <DetailRow label="最新记录">{formatTime(lastEvent?.at_ms)}</DetailRow>
            </dl>
            <details className="sr-disclosure"><summary>查看任务标识</summary><dl><DetailRow label="事件编号" mono>{task.request.event_id}</DetailRow><DetailRow label="投递标识" mono>{task.request.delivery_key}</DetailRow><DetailRow label="原会话" mono>{task.request.session_key}</DetailRow></dl></details>
          </section>
          <section className="sr-card compact">
            <CardHeader title="原始诊断" />
            <p className="sr-diagnosis-preview">{task.request.diagnosis}</p>
            {task.request.error_location && <div className="sr-note-box"><strong>错误位置</strong><p>{task.request.error_location}</p></div>}
            <details className="sr-disclosure"><summary>查看完整诊断</summary><p className="sr-long-text">{task.request.diagnosis}</p></details>
          </section>
        </aside>
      </div>
    </>}
  </main>;
}
