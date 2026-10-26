import React, {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import styled from 'styled-components';
import CollaborationDefinitionGraph from './CollaborationDefinitionGraph';
import HighlightedYaml from './HighlightedYaml';
import {
  CollaborationDefinitionSnapshotError,
} from './snapshot';
import type {
  CollaborationDefinitionComponentProps,
  CollaborationDefinitionDiagnostic,
  CollaborationDefinitionParticipant,
  CollaborationDefinitionValidationResult,
} from './types';
import {
  CollaborationDefinitionValidationError,
  validateCollaborationDefinition,
} from './validation';
import { decodeCollaborationDefinitionPayload, definitionVersionKey, readStoredDefinition, validateStoredDefinition, DraftReadError } from './reference';
import type { CollaborationDefinitionPayload, StoredDefinition } from './reference';
import { resolveHostApiBaseUrl } from './api';

const Container = styled.section`
  box-sizing: border-box;
  height: 100%;
  width: 100%;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 18px;
  color: #334155;
  background: #f8fafc;
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
`;

const Content = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  max-width: 1100px;
  margin: 0 auto;
`;

const Header = styled.header`
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 16px;
  background: #ffffff;

  h2 {
    margin: 0;
    overflow-wrap: anywhere;
    color: #0f172a;
    font-size: 18px;
    line-height: 1.4;
  }

  p {
    margin: 5px 0 0;
    overflow-wrap: anywhere;
    color: #64748b;
    font-size: 12px;
  }
`;

const HeaderTop = styled.div`
  display: flex;
  align-items: flex-start;
  gap: 12px;

  h2 {
    flex: 1;
    min-width: 0;
  }
`;

const ValidationStatus = styled.span`
  color: hsl(var(--success, 161 94% 20%));
  white-space: nowrap;
`;

const Section = styled.section`
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 14px;
  background: #ffffff;
`;

const SectionTitle = styled.div`
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;

  h3 {
    margin: 0;
    color: #1e293b;
    font-size: 14px;
  }
`;

const Button = styled.button`
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 6px 10px;
  color: #334155;
  background: #ffffff;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
  white-space: nowrap;

  &:hover:not(:disabled) {
    background: #f8fafc;
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
`;

const Toolbar = styled.div`
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 14px;
`;

const LaunchButton = styled(Button)`
  flex-shrink: 0;
  border-color: transparent;
  padding: 8px 14px;
  color: hsl(var(--primary-foreground, 0 0% 100%));
  background: hsl(var(--primary, 221.2 83.2% 53.3%));
  font-size: 14px;
  font-weight: 600;

  &:hover:not(:disabled) {
    background: hsl(var(--primary, 221.2 83.2% 53.3%) / 0.9);
  }
`;

const Tabs = styled.div`
  display: flex;
  gap: 4px;
  border-radius: 9px;
  padding: 3px;
  background: #f1f5f9;
`;

const Tab = styled(Button)<{ $active: boolean }>`
  border-color: transparent;
  color: ${(props) => props.$active ? '#0f172a' : '#64748b'};
  background: ${(props) => props.$active ? '#ffffff' : 'transparent'};
  box-shadow: ${(props) => props.$active ? '0 1px 3px #0f172a1a' : 'none'};
`;

const TabContent = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-width: 0;
  gap: 16px;

  &[hidden] { display: none; }
`;

const SourceDetails = styled.div`
  margin-top: 12px;
  overflow-wrap: anywhere;
  color: #64748b;
  font-size: 12px;

  p { margin: 5px 0 0; }
`;

const Source = styled.pre`
  max-height: 340px;
  margin: 0;
  overflow: auto;
  border-radius: 9px;
  padding: 13px;
  color: #e2e8f0;
  background: #0f172a;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre;

  code { font: inherit; }
  .hljs-attr { color: #7dd3fc; }
  .hljs-string { color: #a7f3d0; }
  .hljs-number { color: #fdba74; }
  .hljs-literal { color: #c4b5fd; }
  .hljs-comment { color: #94a3b8; font-style: italic; }
  .hljs-meta, .hljs-type, .hljs-template-variable { color: #fcd34d; }
`;

const Notice = styled.div<{ $tone?: 'success' | 'warning' | 'danger' }>`
  border: 1px solid
    ${(props) =>
      props.$tone === 'success'
        ? '#a7f3d0'
        : props.$tone === 'danger'
        ? '#fecaca'
        : '#fde68a'};
  border-radius: 10px;
  padding: 12px;
  color: ${(props) =>
    props.$tone === 'success'
      ? '#065f46'
      : props.$tone === 'danger'
      ? '#991b1b'
      : '#92400e'};
  background: ${(props) =>
    props.$tone === 'success'
      ? '#ecfdf5'
      : props.$tone === 'danger'
      ? '#fef2f2'
      : '#fffbeb'};
  font-size: 13px;
  line-height: 1.55;
`;

const Loading = styled(Notice)`
  position: relative;
  overflow: hidden;

  &::after {
    position: absolute;
    bottom: 0;
    left: 0;
    width: 35%;
    height: 2px;
    background: #8b5cf6;
    content: '';
    animation: collaboration-loading 1.2s ease-in-out infinite alternate;
  }

  @keyframes collaboration-loading {
    from { transform: translateX(-20%); }
    to { transform: translateX(230%); }
  }
`;

const RetryRow = styled.div`
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
`;

const Summary = styled.div`
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;

  div {
    border-radius: 10px;
    padding: 11px;
    text-align: center;
    background: #f1f5f9;
  }

  strong {
    display: block;
    overflow: hidden;
    color: #0f172a;
    font-size: 18px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  span {
    color: #64748b;
    font-size: 11px;
  }
`;

const Grid = styled.div`
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 10px;
`;

const Participant = styled.div`
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  padding: 11px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.5;

  strong {
    display: block;
    color: #1e293b;
    font-size: 13px;
  }
`;

const DiagnosticList = styled.ul`
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;

  li {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 9px 10px;
    color: #475569;
    background: #ffffff;
    font-size: 12px;
    line-height: 1.5;
  }

  code {
    color: #6d28d9;
  }
`;

type PreviewState =
  | { status: 'validating' }
  | { status: 'empty'; checkedAt: number }
  | {
      status: 'complete';
      result: CollaborationDefinitionValidationResult;
      checkedAt: number;
      validator?: string;
    }
  | { status: 'failed'; message: string; checkedAt: number };

function readSnapshot(
  props: CollaborationDefinitionComponentProps,
): CollaborationDefinitionPayload {
  const raw =
    props.snapshot ??
    props.data?.snapshot ??
    props.params?.data?.snapshot ??
    props.payload?.data?.snapshot;
  return decodeCollaborationDefinitionPayload(raw);
}

function readSourceMessageId(
  props: CollaborationDefinitionComponentProps,
  snapshot: CollaborationDefinitionPayload,
): string {
  const raw =
    props.sourceMessageId ??
    props.data?.sourceMessageId ??
    props.params?.data?.sourceMessageId ??
    props.payload?.data?.sourceMessageId;
  return typeof raw === 'string' && raw ? raw : `version-${definitionVersionKey(snapshot)}`;
}

function diagnosticText(item: CollaborationDefinitionDiagnostic): string {
  const path = item.path ? ` · ${item.path}` : '';
  const hint = item.hint ? `；建议：${item.hint}` : '';
  return `${item.message}${path}${hint}`;
}

function Diagnostics({
  title,
  items,
}: {
  title: string;
  items: CollaborationDefinitionDiagnostic[];
}) {
  if (!items.length) return null;
  return (
    <Section>
      <SectionTitle><h3>{title}</h3></SectionTitle>
      <DiagnosticList>
        {items.map((item, index) => (
          <li key={`${item.code}:${item.path}:${index}`}>
            <code>{item.code}</code> · {diagnosticText(item)}
          </li>
        ))}
      </DiagnosticList>
    </Section>
  );
}
function Participants({
  participants,
}: {
  participants: CollaborationDefinitionParticipant[];
}) {
  if (!participants.length) return null;
  return (
    <Section>
      <SectionTitle><h3>逻辑参与者槽位</h3></SectionTitle>
      <p style={{ margin: '0 0 10px', color: '#64748b', fontSize: 12 }}>
        槽位仅描述流程角色，不代表真实 Bot 已完成绑定。
      </p>
      <Grid>
        {participants.map((participant) => (
          <Participant key={participant.binding}>
            <strong>{participant.display_name || participant.binding}</strong>
            <div>
              {participant.required ? '必需槽位' : '可选槽位'} ·{' '}
              {participant.assigned ? '定义中已有分配' : '尚未分配'}
            </div>
            {participant.description ? <div>{participant.description}</div> : null}
          </Participant>
        ))}
      </Grid>
    </Section>
  );
}

function ValidationResult({ result }: { result: CollaborationDefinitionValidationResult }) {
  return (
    <>
      {!result.valid ? <Notice $tone="danger">定义校验未通过</Notice> : null}
      {result.valid && result.graph ? (
        <CollaborationDefinitionGraph
          graph={result.graph}
          initialNodes={result.summary.initial_nodes}
        />
      ) : result.valid ? (
        <Notice>校验通过，但当前服务未返回流程图。</Notice>
      ) : null}
      <Section>
        <SectionTitle><h3>校验摘要</h3></SectionTitle>
        <Summary>
          <div><strong>{result.summary.participants}</strong><span>参与者槽位</span></div>
          <div><strong>{result.summary.nodes}</strong><span>流程节点</span></div>
          <div><strong>{result.summary.final_output_node || '—'}</strong><span>最终输出</span></div>
        </Summary>
      </Section>
      <Diagnostics title="错误" items={result.errors} />
      <Diagnostics title="警告" items={result.warnings} />
      <Participants participants={result.participants} />
    </>
  );
}

export default function CollaborationDefinitionPreview(
  props: CollaborationDefinitionComponentProps,
) {
  const baseUrl = resolveHostApiBaseUrl(props) ?? '/bcnproxy';
  const decoded = useMemo(() => {
    try {
      const snapshot = readSnapshot(props);
      return {
        snapshot,
        sourceMessageId: readSourceMessageId(props, snapshot),
        error: '',
      };
    } catch (error) {
      return {
        snapshot: null,
        sourceMessageId: '',
        error:
          error instanceof CollaborationDefinitionSnapshotError
            ? error.message
            : '副屏快照数据无效',
      };
    }
  }, [props]);
  const [state, setState] = useState<PreviewState>({ status: 'validating' });
  const [copied, setCopied] = useState(false);
  const [launchError, setLaunchError] = useState('');
  const [activeTab, setActiveTab] = useState<'preview' | 'source'>('preview');
  const tabId = useId();
  const [loaded, setLoaded] = useState<{ key: string; source: StoredDefinition }>();
  const requestRef = useRef<{ id: number; controller: AbortController }>();
  const requestIdRef = useRef(0);
  const requestKeyRef = useRef('');
  const snapshot = decoded.snapshot;
  const identityKey = snapshot
    ? `${baseUrl}|${decoded.sourceMessageId}|${JSON.stringify(snapshot)}`
    : decoded.error;

  const runValidation = useCallback(async () => {
    requestRef.current?.controller.abort();
    const request = {
      id: ++requestIdRef.current,
      controller: new AbortController(),
    };
    requestRef.current = request;
    requestKeyRef.current = identityKey;
    if (!snapshot) {
      setState({ status: 'failed', message: decoded.error, checkedAt: Date.now() });
      return;
    }
    setLoaded(undefined);
    if (snapshot.schemaVersion === 1 && !snapshot.definitionYaml.trim()) {
      setState({ status: 'empty', checkedAt: Date.now() });
      return;
    }
    setState({ status: 'validating' });
    try {
      const current = () => requestRef.current?.id === request.id && !request.controller.signal.aborted;
      if (snapshot.schemaVersion === 2) {
        const source = await readStoredDefinition(baseUrl, snapshot, request.controller.signal);
        if (!current()) return;
        setLoaded({ key: identityKey, source });
        const validation = await validateStoredDefinition(baseUrl, snapshot, source, request.controller.signal);
        if (current()) setState({ status: 'complete', ...validation });
      } else {
        const result = await validateCollaborationDefinition(baseUrl, snapshot.definitionYaml, request.controller.signal);
        if (current()) setState({ status: 'complete', result, checkedAt: Date.now() });
      }
    } catch (error) {
      if (request.controller.signal.aborted || requestRef.current?.id !== request.id) return;
      setState({
        status: 'failed',
        message:
          (error instanceof CollaborationDefinitionValidationError || error instanceof DraftReadError)
            ? error.message
            : 'YAML 校验请求失败',
        checkedAt: Date.now(),
      });
    }
  }, [decoded.error, snapshot, identityKey, baseUrl]);

  useEffect(() => {
    setActiveTab('preview');
    setLaunchError('');
    setCopied(false);
    void runValidation();
    return () => {
      requestRef.current?.controller.abort();
      requestIdRef.current += 1;
    };
  }, [identityKey]);


  const source = snapshot?.schemaVersion === 1 ? snapshot : loaded?.key === identityKey ? loaded.source : undefined;
  const copySource = async () => {
    if (!source) return;
    try {
      await navigator.clipboard.writeText(source.definitionYaml);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  const visibleState: PreviewState = requestKeyRef.current === identityKey ? state : { status: 'validating' };
  const checkedAt = visibleState.status === 'validating' ? null : visibleState.checkedAt;

  return (
    <Container>
      <Content>
        <Header>
          <HeaderTop>
            <h2>{source?.title || '自定义协作'}</h2>
            {snapshot?.schemaVersion === 2 ? <LaunchButton type="button"
              disabled={!source || visibleState.status !== 'complete' || !visibleState.result.valid}
              onClick={() => {
                if (snapshot.schemaVersion !== 2) return;
                if (!props.onOpenCreateGroup) {
                  setLaunchError('当前宿主尚未接入建群入口，可继续预览和复制源码。');
                  return;
                }
                try { props.onOpenCreateGroup({ draft: { ...snapshot } }); setLaunchError(''); }
                catch { setLaunchError('建群浮层未能打开，请稍后重试'); }
              }}>发起协作</LaunchButton> : null}
          </HeaderTop>
          <p>
            {snapshot?.schemaVersion === 2 ? `固定版本 ${snapshot.revision} · 只读预览` : 'YAML 快照 · 只读预览'}
            {visibleState.status === 'complete' && visibleState.result.valid ? (
              <ValidationStatus> · 定义校验通过</ValidationStatus>
            ) : null}
          </p>
          <Toolbar>
            <Tabs role="tablist" aria-label="协作定义视图" onKeyDown={(event) => {
              let next: 'preview' | 'source';
              if (event.key === 'Home') next = 'preview';
              else if (event.key === 'End') next = 'source';
              else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') next = activeTab === 'preview' ? 'source' : 'preview';
              else return;
              event.preventDefault();
              setActiveTab(next);
              document.getElementById(`${tabId}-${next}`)?.focus();
            }}>
              {(['preview', 'source'] as const).map((tab) => (
                <Tab key={tab} type="button" role="tab" id={`${tabId}-${tab}`}
                  aria-selected={activeTab === tab} aria-controls={`${tabId}-${tab}-panel`}
                  tabIndex={activeTab === tab ? 0 : -1} $active={activeTab === tab}
                  onClick={() => setActiveTab(tab)}>
                  {tab === 'preview' ? '协作预览' : '查看定义'}
                </Tab>
              ))}
            </Tabs>
          </Toolbar>
        </Header>

        {launchError ? <Notice role="alert">{launchError}</Notice> : null}
        {visibleState.status === 'validating' ? <Loading>正在读取并校验协作定义…</Loading> : null}
        {visibleState.status === 'empty' ? (
          <Notice>YAML 为空，已保留原文且未向服务端提交校验。</Notice>
        ) : null}
        {visibleState.status === 'failed' ? (
          <Notice $tone="danger">
            <RetryRow>
              <span>{visibleState.message}</span>
              {snapshot ? (
                <Button type="button" onClick={() => void runValidation()}>
                  重新校验
                </Button>
              ) : null}
            </RetryRow>
          </Notice>
        ) : null}
        <TabContent role="tabpanel" id={`${tabId}-preview-panel`}
          aria-labelledby={`${tabId}-preview`} tabIndex={0} hidden={activeTab !== 'preview'}>
          {activeTab === 'preview' && visibleState.status === 'complete' ? (
            <ValidationResult result={visibleState.result} />
          ) : null}
        </TabContent>
        <TabContent role="tabpanel" id={`${tabId}-source-panel`}
          aria-labelledby={`${tabId}-source`} tabIndex={0} hidden={activeTab !== 'source'}>
          {activeTab === 'source' && source ? (
            <Section>
              <SectionTitle>
                <h3>协作定义 YAML</h3>
                <Button type="button" aria-label="复制源码" onClick={() => void copySource()}>
                  {copied ? '已复制' : '复制源码'}
                </Button>
              </SectionTitle>
              <Source><HighlightedYaml source={source.definitionYaml} /></Source>
              <SourceDetails>
                {snapshot ? <p>{snapshot.schemaVersion === 2 ? '存储版本' : '快照'} {definitionVersionKey(snapshot)}</p> : null}
                {loaded?.key === identityKey ? <p>SHA-256 {loaded.source.contentHash} · {loaded.source.sizeBytes} 字节</p> : null}
                {visibleState.status === 'complete' && visibleState.validator ? <p>校验器 {visibleState.validator}</p> : null}
                {decoded.sourceMessageId ? <p>来源消息 {decoded.sourceMessageId}</p> : null}
                {checkedAt ? <p>校验时间：{new Date(checkedAt).toLocaleString()}</p> : null}
              </SourceDetails>
            </Section>
          ) : null}
        </TabContent>
      </Content>
    </Container>
  );
}
