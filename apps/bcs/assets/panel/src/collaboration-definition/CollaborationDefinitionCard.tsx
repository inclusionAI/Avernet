import React, { useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';
import {
  buildCollaborationDefinitionTabId,
  COLLABORATION_DEFINITION_PREVIEW_ENTRY,
  CollaborationDefinitionSnapshotError,
  snapshotInputFromProps,
} from './snapshot';
import type { CollaborationDefinitionComponentProps } from './types';
import { decodeCollaborationDefinitionPayload, definitionVersionKey } from './reference';
import { resolveHostApiBaseUrl } from './api';

const Card = styled.article`
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  padding: 14px;
  color: #334155;
  background: #ffffff;
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
`;

const Heading = styled.div`
  display: flex;
  gap: 11px;
  align-items: flex-start;
`;

const Icon = styled.div`
  display: inline-flex;
  width: 38px;
  height: 38px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  color: #6d28d9;
  background: #ede9fe;
  font-size: 19px;
`;

const Title = styled.div`
  min-width: 0;
  flex: 1;

  strong {
    display: block;
    overflow: hidden;
    color: #1e293b;
    font-size: 14px;
    font-weight: 650;
    line-height: 1.45;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  span {
    display: block;
    margin-top: 3px;
    color: #64748b;
    font-size: 12px;
  }
`;

const Notice = styled.div`
  margin-top: 11px;
  border: 1px solid #fde68a;
  border-radius: 8px;
  padding: 8px 10px;
  color: #92400e;
  background: #fffbeb;
  font-size: 12px;
  line-height: 1.5;
`;

const OpenButton = styled.button`
  width: 100%;
  margin-top: 12px;
  border: 1px solid #7c3aed;
  border-radius: 8px;
  padding: 8px 12px;
  color: #ffffff;
  background: #7c3aed;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  font-weight: 650;

  &:hover:not(:disabled) {
    background: #6d28d9;
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
`;

interface OpenSnapshotEntry {
  fingerprint: string;
  owners: Set<symbol>;
}

const openSnapshots = new Map<string, OpenSnapshotEntry>();
let mountedSourceCounter = 0;

function snapshotError(error: unknown): string {
  if (error instanceof CollaborationDefinitionSnapshotError) return error.message;
  return '协作快照格式无效';
}

function sourceMessageId(element: HTMLElement | null, fallback: string): string {
  const message = element?.closest<HTMLElement>('[data-message-id]');
  return message?.dataset.messageId || fallback;
}

function bcsPanelCdn(): string | undefined {
  return (
    window.aixGetLibraryCdn?.('bcsPanel') ||
    window.aixLibraryCdnMap?.get?.('bcsPanel')
  );
}

export default function CollaborationDefinitionCard(
  props: CollaborationDefinitionComponentProps,
) {
  const apiBaseUrl = resolveHostApiBaseUrl(props);
  const rootRef = useRef<HTMLElement>(null);
  const ownerRef = useRef(Symbol('collaboration-definition-card'));
  const fallbackSource = useRef(`mounted-${++mountedSourceCounter}`);
  const openedTabRef = useRef<string>();
  const [openError, setOpenError] = useState('');
  const decoded = useMemo(() => {
    try {
      return {
        snapshot: decodeCollaborationDefinitionPayload(
          snapshotInputFromProps(props),
        ),
        error: '',
      };
    } catch (error) {
      return { snapshot: null, error: snapshotError(error) };
    }
  }, [props]);

  const fingerprint = decoded.snapshot ? JSON.stringify(decoded.snapshot) : '';
  useEffect(
    () => () => {
      const tabId = openedTabRef.current;
      if (!tabId) return;
      const entry = openSnapshots.get(tabId);
      entry?.owners.delete(ownerRef.current);
      if (!entry?.owners.size) {
        openSnapshots.delete(tabId);
        window.aixBridge?.closePanelTab(tabId);
      }
    },
    [decoded.snapshot?.schemaVersion === 2 ? fingerprint : decoded.snapshot?.snapshotId, apiBaseUrl],
  );

  const openPreview = () => {
    const snapshot = decoded.snapshot;
    if (!snapshot) return;
    const bridge = window.aixBridge;
    const cdn = bcsPanelCdn();
    if (!bridge || !cdn) {
      setOpenError('当前宿主尚未准备好 BCS 副屏，请稍后重试');
      return;
    }
    const messageId = sourceMessageId(rootRef.current, fallbackSource.current);
    const tabId = buildCollaborationDefinitionTabId(
      `${window.location.origin}${window.location.pathname}${window.location.search}|${cdn}|${apiBaseUrl ?? ''}|${messageId}`,
      definitionVersionKey(snapshot),
    );
    let existing = openSnapshots.get(tabId);
    if (existing && existing.fingerprint !== fingerprint) {
      if (snapshot.schemaVersion === 1) {
        setOpenError('相同来源和快照 ID 对应了不同内容，已拒绝覆盖现有预览');
        return;
      }
      bridge.closePanelTab(tabId);
      openSnapshots.delete(tabId);
      existing = undefined;
    }
    const entry = existing || { fingerprint, owners: new Set<symbol>() };
    entry.owners.add(ownerRef.current);
    openSnapshots.set(tabId, entry);
    openedTabRef.current = tabId;
    const panelPayload = {
      cdn,
      entry: COLLABORATION_DEFINITION_PREVIEW_ENTRY,
      data: { snapshot, sourceMessageId: messageId, tabId, ...(apiBaseUrl ? { apiBaseUrl } : {}) },
    };
    const tab = {
      id: tabId,
      title: (snapshot.schemaVersion === 1 && snapshot.title) || '自定义协作',
      type: 'umd',
      params: panelPayload,
      payload: panelPayload,
      closable: true,
    };
    if (existing && !bridge.focusPanelTab) {
      // @aix-chat/core@0.1.5 registers the host focus handler but does not
      // expose it on ChatBridge. Recreating only this read-only tab preserves
      // the expected focus behavior until the public method is available.
      bridge.closePanelTab(tabId);
    }
    bridge.openPanelTab(tab);
    bridge.focusPanelTab?.(tabId);
    setOpenError('');
  };

  return (
    <Card ref={rootRef}>
      <Heading>
        <Icon aria-hidden="true">⌘</Icon>
        <Title>
          <strong>{(decoded.snapshot?.schemaVersion === 1 && decoded.snapshot.title) || '自定义协作'}</strong>
          <span>{decoded.snapshot?.schemaVersion === 2 ? `固定版本 ${decoded.snapshot.revision} · 只读分享` : '完整 YAML 快照 · 只读预览'}</span>
        </Title>
      </Heading>
      {decoded.error || openError ? (
        <Notice role="status">{decoded.error || openError}</Notice>
      ) : null}
      <OpenButton
        type="button"
        disabled={!decoded.snapshot}
        onClick={openPreview}
      >
        预览协作
      </OpenButton>
    </Card>
  );
}
