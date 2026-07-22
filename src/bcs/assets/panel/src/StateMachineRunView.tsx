import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import styled from 'styled-components';

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

type JsonObject = { [key: string]: JsonValue };
type CopyTarget = 'run-id' | 'task-output' | 'node-artifact' | null;

export type StateMachineRunStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'aborted';

export type StateMachineNodeStatus =
  | 'pending'
  | 'ready'
  | 'running'
  | 'judging'
  | 'completed'
  | 'failed'
  | 'retry_scheduled'
  | 'skipped';

export interface StateMachineRun {
  run_id: string;
  definition_id: string;
  definition_version: number;
  group_id?: string;
  group_version?: number;
  session_id?: string;
  created_by?: string;
  status: StateMachineRunStatus;
  input?: JsonValue;
  output?: JsonValue;
  created_at?: number;
  updated_at?: number;
  completed_at?: number;
}

export interface StateMachineDefinition {
  id: string;
  version: number;
  name?: string;
  graph_mode?: string;
  initial_nodes?: string[];
}

export interface StateMachineAssignee {
  type?: string;
  binding?: string;
  [key: string]: JsonValue | undefined;
}

export interface StateMachineNode {
  node_id: string;
  display_name?: string;
  kind?: string;
  assignee?: StateMachineAssignee;
  final_output?: boolean;
  status?: StateMachineNodeStatus;
  attempt?: number;
  assignee_bot_id?: string;
  started_at?: number;
  completed_at?: number;
}

export interface StateMachineNodeDetailNode {
  run_id: string;
  node_id: string;
  status?: StateMachineNodeStatus;
  attempt?: number;
  node_timeout_ms?: number;
  max_attempts?: number;
  assignee_bot_id?: string;
  delivery_request_id?: string;
  bot_delivery_run_id?: string;
  artifact_text?: string;
  error?: string;
  started_at?: number;
  completed_at?: number;
}

export interface StateMachineJudgeOutput {
  node_id: string;
  attempt?: number;
  created_at?: number;
  decision?: JsonValue;
}

export interface StateMachineNodeDetailResponse {
  node: StateMachineNodeDetailNode;
  judge_outputs?: StateMachineJudgeOutput[];
}

export interface StateMachineEdge {
  source: string;
  outcome?: string;
  target: string;
}

export interface StateMachineRunGraph {
  run: StateMachineRun;
  definition: StateMachineDefinition;
  nodes: StateMachineNode[];
  edges: StateMachineEdge[];
}

export interface StateMachineRunViewData {
  runId?: string;
  stateMachineRunId?: string;
  smRunId?: string;
  apiBaseUrl?: string;
  baseUrl?: string;
}

export interface StateMachineRunViewProps extends StateMachineRunViewData {
  data?: StateMachineRunViewData;
  pollingInterval?: number;
  autoRefresh?: boolean;
  className?: string;
  style?: CSSProperties;
  onInteraction?: (payload: {
    type: string;
    node?: StateMachineNode;
    run?: StateMachineRun;
  }) => void;
}

interface LayoutNode {
  node: StateMachineNode;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface GraphLayout {
  nodes: LayoutNode[];
  edges: Array<{
    edge: StateMachineEdge;
    source: LayoutNode;
    target: LayoutNode;
  }>;
  width: number;
  height: number;
}

const DEFAULT_BASE_URL = '/bcnproxy';
const DEFAULT_POLLING_INTERVAL = 3000;
const MAX_TRANSIENT_RETRIES = 3;
const NODE_WIDTH = 188;
const NODE_HEIGHT = 58;
const LEVEL_GAP = 56;
const COLUMN_GAP = 18;
const PADDING = 28;

const RUN_ACTIVE_STATUSES = new Set<string>(['pending', 'running']);
const RUN_TERMINAL_STATUSES = new Set<string>([
  'completed',
  'failed',
  'aborted',
]);
const NODE_ACTIVE_STATUSES = new Set<string>([
  'pending',
  'ready',
  'running',
  'judging',
  'retry_scheduled',
]);
const NODE_TERMINAL_STATUSES = new Set<string>([
  'completed',
  'failed',
  'skipped',
]);

const Container = styled.section`
  box-sizing: border-box;
  display: flex;
  position: relative;
  height: 100%;
  min-height: 0;
  width: 100%;
  flex-direction: column;
  overflow: hidden;
  padding: 20px;
  color: #1f2937;
  background: linear-gradient(
      135deg,
      rgba(248, 250, 252, 0.97) 0%,
      rgba(241, 245, 249, 0.95) 50%,
      rgba(255, 255, 255, 1) 100%
    ),
    #ffffff;
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
`;

const Header = styled.header`
  display: grid;
  flex: none;
  gap: 12px;
  margin-bottom: 16px;
  border: 1px solid rgba(226, 232, 240, 0.8);
  border-radius: 12px;
  padding: 16px 18px;
  background: linear-gradient(
      180deg,
      rgba(255, 255, 255, 0.95) 0%,
      rgba(248, 250, 252, 0.9) 100%
    ),
    #ffffff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04),
    0 4px 12px rgba(15, 23, 42, 0.02);
  backdrop-filter: blur(8px);
  transition: box-shadow 200ms ease, border-color 200ms ease;

  &:hover {
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06),
      0 6px 20px rgba(15, 23, 42, 0.03);
    border-color: rgba(203, 213, 225, 0.9);
  }
`;

const HeaderTop = styled.div`
  display: flex;
  min-width: 0;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;

  @media (max-width: 520px) {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }
`;

const ScrollArea = styled.main`
  min-height: 0;
  flex: 1;
  overflow-x: hidden;
  overflow-y: auto;
  padding-right: 2px;
`;

const TitleGroup = styled.div`
  min-width: 0;
`;

const Eyebrow = styled.div`
  margin-bottom: 6px;
  color: #64748b;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0;
  text-transform: uppercase;
`;

const Title = styled.h1`
  margin: 0;
  overflow-wrap: anywhere;
  color: #0f172a;
  font-size: 20px;
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: -0.01em;
`;

const HeaderInfo = styled.div`
  display: grid;
  min-width: 0;
  gap: 10px;
  border-top: 1px solid rgba(226, 232, 240, 0.88);
  padding-top: 12px;
`;

const SubTitle = styled.div`
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  color: #475569;
  font-size: 13px;
  line-height: 1.5;
`;

const SubTitleText = styled.span`
  min-width: 0;
  color: #64748b;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-weight: 650;
  overflow-wrap: anywhere;
`;

const HeaderMetaRow = styled.div`
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: center;
`;

const HeaderMetaItem = styled.span`
  display: inline-flex;
  min-width: 0;
  gap: 7px;
  align-items: baseline;
  color: #475569;
  font-size: 12px;
  line-height: 1.35;

  & + & {
    border-left: 1px solid #e2e8f0;
    padding-left: 16px;
  }

  @media (max-width: 520px) {
    & + & {
      border-left: 0;
      padding-left: 0;
    }
  }
`;

const HeaderMetaLabel = styled.span`
  color: #94a3b8;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.04em;
  text-transform: uppercase;
`;

const HeaderMetaValue = styled.span`
  min-width: 0;
  overflow: hidden;
  color: #334155;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const InlineCopyButton = styled.button`
  display: inline-flex;
  flex: none;
  gap: 4px;
  align-items: center;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  padding: 2px 8px;
  color: #64748b;
  background: #ffffff;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.35;
  cursor: pointer;
  transition: all 150ms cubic-bezier(0.4, 0, 0.2, 1);

  &:hover {
    border-color: #bfdbfe;
    color: #2563eb;
    background: #eff6ff;
    transform: translateY(-1px);
    box-shadow: 0 2px 6px rgba(37, 99, 235, 0.08);
  }

  &:active {
    transform: translateY(0);
  }
`;

const Actions = styled.div`
  display: flex;
  flex: none;
  gap: 8px;
  align-items: center;

  @media (max-width: 420px) {
    width: 100%;
    justify-content: space-between;
  }
`;

const IconButton = styled.button`
  display: inline-flex;
  width: 34px;
  height: 34px;
  align-items: center;
  justify-content: center;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  color: #475569;
  background: #ffffff;
  cursor: pointer;
  transition: all 180ms cubic-bezier(0.4, 0, 0.2, 1);

  &:hover {
    border-color: #2563eb;
    color: #1d4ed8;
    background: #f8faff;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.12);
    transform: translateY(-1px);
  }

  &:active {
    transform: translateY(0);
    box-shadow: 0 2px 6px rgba(37, 99, 235, 0.1);
  }

  &:disabled {
    color: #94a3b8;
    cursor: not-allowed;
    box-shadow: none;
    transform: none;
  }
`;

const StatusPill = styled.span<{ $tone: StatusTone }>`
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid ${(props) => props.$tone.border};
  border-radius: 999px;
  padding: 4px 12px;
  color: ${(props) => props.$tone.text};
  background: ${(props) => props.$tone.bg};
  font-size: 12px;
  font-weight: 600;
  line-height: 1.3;
  white-space: nowrap;
  letter-spacing: 0.02em;
  text-transform: capitalize;
  transition: transform 150ms ease, box-shadow 150ms ease;

  &:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 8px ${(props) => props.$tone.border};
  }
`;

const NodeMetaRow = styled.div`
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 5px;
  justify-content: space-between;
  color: #64748b;
  font-size: 11px;
  line-height: 16px;
`;

const BotIdText = styled.span`
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const RoleTag = styled.span`
  display: inline-flex;
  box-sizing: border-box;
  max-width: 52px;
  flex: none;
  align-items: center;
  border: 1px solid #dbe3f2;
  border-radius: 999px;
  padding: 0 5px;
  color: #475569;
  background: #f8fafc;
  font-size: 10px;
  font-weight: 600;
  line-height: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const ContentGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 18px;
`;

const GraphShell = styled.div`
  min-width: 0;
  overflow: auto;
  border: 1px solid rgba(226, 232, 240, 0.7);
  border-radius: 12px;
  background: radial-gradient(
      circle at 50% 50%,
      rgba(248, 250, 252, 0.5) 0%,
      transparent 70%
    ),
    linear-gradient(#f1f5f9 1px, transparent 1px),
    linear-gradient(90deg, #f1f5f9 1px, transparent 1px), #fafbfd;
  background-size: auto, 24px 24px, 24px 24px, auto;
  box-shadow: inset 0 2px 4px rgba(15, 23, 42, 0.02);
`;

const GraphSvg = styled.svg`
  display: block;
  height: auto;
  margin: 0 auto;
  max-width: none;
  width: auto;
`;

const NodeGroup = styled.g`
  cursor: pointer;
  outline: none;
  transition: filter 180ms ease;

  &:hover {
    filter: drop-shadow(0 4px 12px rgba(15, 23, 42, 0.1));
  }

  &:hover rect:first-of-type {
    stroke-width: 1.6;
  }

  &:focus-visible rect:first-of-type {
    stroke: #2563eb;
    stroke-width: 2;
  }

  &:active {
    filter: drop-shadow(0 2px 6px rgba(15, 23, 42, 0.08));
  }
`;

const Panel = styled.aside`
  min-width: 0;
  border: 1px solid rgba(226, 232, 240, 0.7);
  border-radius: 12px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(8px);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03),
    0 4px 12px rgba(15, 23, 42, 0.02);
`;

const DetailSection = styled.section`
  & + & {
    margin-top: 18px;
    border-top: 1px solid #e2e8f0;
    padding-top: 18px;
  }
`;

const SectionTitle = styled.h3`
  margin: 0 0 12px;
  color: #0f172a;
  font-size: 15px;
  font-weight: 700;
`;

const SectionHeader = styled.div`
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;

  ${SectionTitle} {
    margin: 0;
  }
`;

const CopyButton = styled.button`
  display: inline-flex;
  flex: none;
  gap: 5px;
  align-items: center;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 5px 10px;
  color: #475569;
  background: #ffffff;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.2;
  cursor: pointer;
  transition: all 150ms cubic-bezier(0.4, 0, 0.2, 1);

  &:hover {
    border-color: #93c5fd;
    color: #2563eb;
    background: #eff6ff;
    box-shadow: 0 2px 8px rgba(37, 99, 235, 0.08);
    transform: translateY(-1px);
  }

  &:active {
    transform: translateY(0);
    box-shadow: 0 1px 4px rgba(37, 99, 235, 0.06);
  }

  &:disabled {
    color: #94a3b8;
    cursor: not-allowed;
    box-shadow: none;
    transform: none;
  }
`;

const NodeDetailHero = styled.div`
  min-width: 0;
  border: 1px solid rgba(226, 232, 240, 0.7);
  border-radius: 12px;
  padding: 14px;
  background: linear-gradient(
      180deg,
      rgba(255, 255, 255, 0.95) 0%,
      rgba(248, 250, 252, 0.8) 100%
    ),
    #ffffff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03),
    0 0 0 1px rgba(255, 255, 255, 0.6) inset;
`;

const NodeTagRow = styled.div`
  display: flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 7px;
  align-items: center;
`;

const NodeTag = styled.span<{ $tone: 'type' | 'role' }>`
  display: inline-flex;
  max-width: 100%;
  gap: 6px;
  align-items: center;
  border: 1px solid
    ${(props) => (props.$tone === 'type' ? '#dbeafe' : '#fed7aa')};
  border-radius: 999px;
  padding: 4px 9px;
  background: ${(props) => (props.$tone === 'type' ? '#eef2ff' : '#fff7ed')};
  white-space: nowrap;
`;

const NodeTagLabel = styled.span<{ $tone: 'type' | 'role' }>`
  color: ${(props) => (props.$tone === 'type' ? '#818cf8' : '#f59e0b')};
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 1.2;
  text-transform: uppercase;
`;

const NodeTagValue = styled.span<{ $tone: 'type' | 'role' }>`
  min-width: 0;
  overflow: hidden;
  color: ${(props) => (props.$tone === 'type' ? '#4f46e5' : '#ea580c')};
  font-size: 11px;
  font-weight: 750;
  line-height: 1.2;
  text-overflow: ellipsis;
`;

const NodeSummaryStatus = styled.span`
  display: inline-flex;
  flex: none;
  margin-left: auto;
`;

const NodeStatusSummaryPill = styled.span<{ $tone: StatusTone }>`
  display: inline-flex;
  gap: 6px;
  align-items: center;
  border: 1px solid ${(props) => props.$tone.border};
  border-radius: 999px;
  padding: 4px 10px;
  color: ${(props) => props.$tone.text};
  background: ${(props) => props.$tone.bg};
  font-size: 11px;
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: 0;
  text-transform: uppercase;
`;

const NodeStatusDot = styled.span<{ $tone: StatusTone }>`
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 999px;
  background: ${(props) => props.$tone.stroke};
`;

const NodeTimeGrid = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr) 20px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  margin-top: 12px;

  @media (max-width: 520px) {
    grid-template-columns: minmax(0, 1fr);
  }
`;

const NodeMetricGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 8px;

  @media (max-width: 520px) {
    grid-template-columns: minmax(0, 1fr);
  }
`;

const NodeSummaryArrow = styled.div`
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #94a3b8;

  @media (max-width: 520px) {
    display: none;
  }
`;

const NodeInfoCard = styled.div`
  display: flex;
  min-width: 0;
  gap: 8px;
  align-items: center;
  border: 1px solid rgba(226, 232, 240, 0.7);
  border-radius: 10px;
  padding: 9px 12px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.02);
  transition: border-color 150ms ease, box-shadow 150ms ease;

  &:hover {
    border-color: rgba(203, 213, 225, 0.9);
    box-shadow: 0 2px 6px rgba(15, 23, 42, 0.04);
  }
`;

const NodeInfoIcon = styled.span<{ $tone: 'time' | 'attempt' | 'duration' }>`
  display: inline-flex;
  width: 24px;
  height: 24px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  color: ${(props) =>
    props.$tone === 'duration'
      ? '#b45309'
      : props.$tone === 'attempt'
      ? '#2563eb'
      : '#94a3b8'};
  background: ${(props) =>
    props.$tone === 'duration'
      ? '#fef3c7'
      : props.$tone === 'attempt'
      ? '#dbeafe'
      : '#f1f5f9'};
`;

const NodeInfoStack = styled.span`
  display: grid;
  min-width: 0;
  gap: 1px;
`;

const NodeInfoLabel = styled.span`
  overflow: hidden;
  color: #94a3b8;
  font-size: 10px;
  font-weight: 800;
  line-height: 1.2;
  text-overflow: ellipsis;
  text-transform: uppercase;
  white-space: nowrap;
`;

const NodeInfoValue = styled.span`
  min-width: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 13px;
  font-weight: 750;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const NodeMetricValue = styled.span`
  display: inline-flex;
  min-width: 0;
  gap: 4px;
  align-items: baseline;
`;

const NodeMetricUnit = styled.span`
  color: #64748b;
  font-size: 11px;
  font-weight: 650;
  line-height: 1.2;
`;

const FinalOutputNote = styled.div`
  margin-top: 9px;
  color: #7c3aed;
  font-size: 11px;
  font-weight: 700;
`;

const RuntimeRows = styled.div`
  display: grid;
  border: 1px solid rgba(226, 232, 240, 0.7);
  border-radius: 10px;
  overflow: hidden;
  background: linear-gradient(
      180deg,
      rgba(255, 255, 255, 0.95) 0%,
      rgba(248, 250, 252, 0.85) 100%
    ),
    #ffffff;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
`;

const RuntimeRow = styled.div`
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  gap: 10px;
  align-items: baseline;
  color: #334155;
  padding: 9px 10px;
  font-size: 12px;

  & + & {
    border-top: 1px solid rgba(226, 232, 240, 0.86);
  }
`;

const RuntimeLabel = styled.div`
  color: #94a3b8;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.04em;
  line-height: 1.35;
  text-transform: uppercase;
`;

const RuntimeValue = styled.div`
  min-width: 0;
  color: #475569;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  font-weight: 650;
  line-height: 1.45;
  overflow-wrap: anywhere;
`;

const RuntimeStrongValue = styled(RuntimeValue)`
  color: #2563eb;
`;

const CollapsibleHeader = styled.div`
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
`;

const CollapsibleToggle = styled.button`
  display: inline-flex;
  min-width: 0;
  gap: 7px;
  align-items: center;
  border: 0;
  padding: 0;
  color: #0f172a;
  background: transparent;
  font: inherit;
  cursor: pointer;
`;

const ToggleIcon = styled.span<{ $expanded: boolean }>`
  display: inline-flex;
  width: 18px;
  height: 18px;
  flex: none;
  align-items: center;
  justify-content: center;
  color: #64748b;
  transform: rotate(${(props) => (props.$expanded ? '90deg' : '0deg')});
  transition: transform 120ms ease;
`;

const CollapsibleTitle = styled.span`
  overflow: hidden;
  color: #0f172a;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const OutputBlock = styled.pre`
  max-height: 280px;
  margin: 0;
  overflow: auto;
  border: 1px solid rgba(226, 232, 240, 0.7);
  border-radius: 10px;
  padding: 14px;
  color: #1e293b;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  font-family: 'SF Mono', 'JetBrains Mono', Consolas, 'Liberation Mono',
    monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.03);
`;

const OutputPlaceholder = styled.div`
  border: 1px dashed #e2e8f0;
  border-radius: 10px;
  padding: 14px;
  color: #94a3b8;
  background: rgba(248, 250, 252, 0.5);
  font-size: 13px;
  line-height: 1.5;
  text-align: center;
`;

const JudgeOutputList = styled.div`
  display: grid;
  gap: 14px;
`;

const JudgeOutputCard = styled.article`
  border: 1px solid rgba(226, 232, 240, 0.7);
  border-radius: 12px;
  padding: 14px;
  background: linear-gradient(
      180deg,
      rgba(255, 255, 255, 0.95) 0%,
      rgba(248, 250, 252, 0.8) 100%
    ),
    #ffffff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.03);
  transition: box-shadow 200ms ease, border-color 200ms ease;

  &:hover {
    box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05);
    border-color: rgba(203, 213, 225, 0.9);
  }
`;

const JudgeMetaRow = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
`;

const JudgeMetaText = styled.div`
  display: inline-flex;
  min-width: 0;
  flex-wrap: wrap;
  gap: 6px;
  align-items: baseline;
  color: #94a3b8;
  font-size: 12px;
  font-weight: 750;
  line-height: 1.35;
  text-transform: uppercase;
`;

const JudgeMetaValue = styled.span`
  color: #334155;
  font-weight: 750;
  text-transform: none;
`;

const JudgeMetaSeparator = styled.span`
  color: #cbd5e1;
  font-weight: 800;
`;

const JudgeStatusPill = styled.span<{ $tone: StatusTone }>`
  display: inline-flex;
  gap: 6px;
  align-items: center;
  border: 1px solid ${(props) => props.$tone.border};
  border-radius: 999px;
  padding: 4px 10px;
  color: ${(props) => props.$tone.text};
  background: ${(props) => props.$tone.bg};
  font-size: 11px;
  font-weight: 800;
  line-height: 1.2;
  letter-spacing: 0;
  text-transform: uppercase;
`;

const JudgeStatusDot = styled.span<{ $tone: StatusTone }>`
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 999px;
  background: ${(props) => props.$tone.stroke};
`;

const DecisionSummary = styled.div`
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.76);
`;

const DecisionHeader = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  border-bottom: 1px solid #e2e8f0;
  padding-bottom: 10px;
`;

const DecisionTitle = styled.div`
  color: #0f172a;
  font-size: 14px;
  font-weight: 750;
`;

const ConfidenceBadge = styled.span`
  display: inline-flex;
  gap: 5px;
  align-items: center;
  border: 1px solid #facc15;
  border-radius: 999px;
  padding: 4px 9px;
  color: #d97706;
  background: #fffbeb;
  font-size: 11px;
  font-weight: 800;
  line-height: 1.2;
`;

const DecisionReason = styled.div`
  color: #475569;
  font-size: 13px;
  line-height: 1.65;
`;

const RetryInstruction = styled.div`
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  gap: 8px;
  margin-top: 12px;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  padding: 10px;
  color: #7c2d12;
  background: rgba(255, 247, 237, 0.82);
`;

const RetryIcon = styled.span`
  display: inline-flex;
  width: 22px;
  height: 22px;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: #ea580c;
  background: #ffedd5;
`;

const RetryTitle = styled.div`
  color: #ea580c;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  line-height: 1.25;
  text-transform: uppercase;
`;

const RetryText = styled.div`
  margin-top: 4px;
  color: #7c2d12;
  font-size: 12px;
  line-height: 1.6;
`;

const CriteriaHeader = styled.div`
  margin-top: 14px;
  color: #94a3b8;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  line-height: 1.3;
  text-transform: uppercase;
`;

const CriteriaList = styled.div`
  display: grid;
  gap: 9px;
  margin-top: 8px;
`;

const CriteriaItem = styled.div<{ $satisfied?: boolean }>`
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  gap: 10px;
  border: 1px solid
    ${(props) =>
      props.$satisfied === undefined
        ? '#e2e8f0'
        : props.$satisfied
        ? '#86efac'
        : '#fca5a5'};
  border-radius: 8px;
  padding: 10px;
  background: ${(props) =>
    props.$satisfied === undefined
      ? 'rgba(248, 250, 252, 0.8)'
      : props.$satisfied
      ? 'rgba(240, 253, 244, 0.76)'
      : 'rgba(254, 242, 242, 0.76)'};
`;

const CriteriaMarker = styled.span<{ $satisfied?: boolean }>`
  display: inline-flex;
  width: 28px;
  height: 24px;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  color: #ffffff;
  background: ${(props) =>
    props.$satisfied === undefined
      ? '#94a3b8'
      : props.$satisfied
      ? '#16a34a'
      : '#dc2626'};
  font-size: 10px;
  font-weight: 800;
  line-height: 1;
`;

const CriteriaTitle = styled.div`
  color: #0f172a;
  font-size: 13px;
  font-weight: 750;
  line-height: 1.4;
`;

const CriteriaEvidence = styled.div`
  margin-top: 6px;
  color: #64748b;
  font-size: 12px;
  line-height: 1.6;
`;

const Message = styled.div`
  border: 1px solid rgba(226, 232, 240, 0.7);
  border-radius: 12px;
  padding: 32px 20px;
  color: #64748b;
  background: rgba(255, 255, 255, 0.8);
  text-align: center;
  font-size: 14px;
`;

const ErrorMessage = styled(Message)`
  border-color: rgba(254, 202, 202, 0.7);
  color: #b91c1c;
  background: rgba(254, 242, 242, 0.8);
`;

const InlineNotice = styled.div<{ $danger?: boolean }>`
  margin: 12px 0 0;
  border: 1px solid ${(props) => (props.$danger ? '#fecaca' : '#dbeafe')};
  border-radius: 8px;
  padding: 10px 12px;
  color: ${(props) => (props.$danger ? '#991b1b' : '#1d4ed8')};
  background: ${(props) => (props.$danger ? '#fff7f7' : '#eff6ff')};
  font-size: 12px;
  line-height: 1.5;
`;

const ModalOverlay = styled.div`
  position: absolute;
  z-index: 20;
  inset: 0;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 24px 16px;
  background: rgba(15, 23, 42, 0.2);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  animation: sm-overlay-in 200ms cubic-bezier(0.4, 0, 0.2, 1);

  @keyframes sm-overlay-in {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
`;

const ModalDialog = styled.div`
  display: flex;
  width: min(680px, 100%);
  max-height: 100%;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(226, 232, 240, 0.8);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.97);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.18),
    0 8px 24px rgba(15, 23, 42, 0.08), 0 0 0 1px rgba(255, 255, 255, 0.5) inset;
  animation: sm-dialog-in 280ms cubic-bezier(0.34, 1.56, 0.64, 1);

  @keyframes sm-dialog-in {
    from {
      opacity: 0;
      transform: translateY(-12px) scale(0.97);
    }
    to {
      opacity: 1;
      transform: translateY(0) scale(1);
    }
  }
`;

const ModalHeader = styled.div`
  display: flex;
  flex: none;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(226, 232, 240, 0.8);
  padding: 12px 16px;
  background: rgba(248, 250, 252, 0.6);
`;

const ModalTitleGroup = styled.div`
  display: flex;
  min-width: 0;
  flex: 1 1 auto;
  gap: 8px;
  align-items: baseline;
`;

const ModalTitle = styled.h2`
  min-width: 0;
  flex: 0 1 auto;
  margin: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 15px;
  font-weight: 750;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const ModalSubtitle = styled.div`
  min-width: 0;
  flex: 1 1 auto;
  margin-top: 0;
  color: #64748b;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
`;

const ModalHeaderActions = styled.div`
  display: inline-flex;
  flex: none;
  gap: 8px;
  align-items: center;
`;

const NodeDetailSpinner = styled.span`
  display: inline-flex;
  width: 16px;
  height: 16px;
  flex: none;
  border: 2px solid rgba(219, 234, 254, 0.6);
  border-top-color: #3b82f6;
  border-radius: 999px;
  animation: node-detail-spin 0.8s cubic-bezier(0.5, 0, 0.5, 1) infinite;

  @keyframes node-detail-spin {
    to {
      transform: rotate(360deg);
    }
  }
`;

const ModalCloseButton = styled(IconButton)`
  width: 30px;
  height: 30px;
  flex: none;
`;

const ModalBody = styled.div`
  min-height: 0;
  overflow: auto;
  padding: 18px;
`;

const LoadingBar = styled.div`
  position: relative;
  overflow: hidden;
  height: 3px;
  margin: -8px 0 14px;
  border-radius: 999px;
  background: rgba(219, 234, 254, 0.6);

  &::after {
    position: absolute;
    inset: 0;
    width: 36%;
    border-radius: inherit;
    background: linear-gradient(90deg, #3b82f6, #60a5fa);
    animation: loading-slide 1.2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
    content: '';
  }

  @keyframes loading-slide {
    0% {
      transform: translateX(-120%);
    }
    100% {
      transform: translateX(360%);
    }
  }
`;

interface StatusTone {
  bg: string;
  border: string;
  text: string;
  stroke: string;
  fill: string;
}

type EdgeState = 'blocked' | 'executed' | 'pending' | 'skipped';

const EDGE_TONES: Record<
  EdgeState,
  {
    label: string;
    marker: string;
    stroke: string;
    strokeDasharray?: string;
    strokeWidth: number;
  }
> = {
  executed: {
    label: '#1d4ed8',
    marker: '#3b82f6',
    stroke: '#3b82f6',
    strokeWidth: 2,
  },
  blocked: {
    label: '#94a3b8',
    marker: '#cbd5e1',
    stroke: '#cbd5e1',
    strokeDasharray: '4 4',
    strokeWidth: 1.5,
  },
  pending: {
    label: '#94a3b8',
    marker: '#cbd5e1',
    stroke: '#cbd5e1',
    strokeDasharray: '4 4',
    strokeWidth: 1.5,
  },
  skipped: {
    label: '#94a3b8',
    marker: '#e2e8f0',
    stroke: '#e2e8f0',
    strokeDasharray: '3 5',
    strokeWidth: 1.2,
  },
};

const EDGE_RENDER_ORDER: Record<EdgeState, number> = {
  skipped: 0,
  pending: 1,
  blocked: 2,
  executed: 3,
};

function normalizeStatus(status?: string) {
  return (status || 'unknown').toLowerCase();
}

function getStatusTone(status?: string): StatusTone {
  const normalized = normalizeStatus(status);

  if (normalized === 'running' || normalized === 'retry_scheduled') {
    return {
      bg: '#eff6ff',
      border: '#bfdbfe',
      text: '#1d4ed8',
      stroke: '#2563eb',
      fill: '#dbeafe',
    };
  }

  if (normalized === 'judging') {
    return {
      bg: '#faf5ff',
      border: '#e9d5ff',
      text: '#7e22ce',
      stroke: '#9333ea',
      fill: '#f3e8ff',
    };
  }

  if (normalized === 'ready') {
    return {
      bg: '#eef2ff',
      border: '#c7d2fe',
      text: '#4338ca',
      stroke: '#4f46e5',
      fill: '#e0e7ff',
    };
  }

  if (
    normalized === 'completed' ||
    normalized === 'success' ||
    normalized === 'succeeded'
  ) {
    return {
      bg: '#ecfdf5',
      border: '#bbf7d0',
      text: '#047857',
      stroke: '#16a34a',
      fill: '#dcfce7',
    };
  }

  if (
    normalized === 'failed' ||
    normalized === 'aborted' ||
    normalized === 'error'
  ) {
    return {
      bg: '#fef2f2',
      border: '#fecaca',
      text: '#b91c1c',
      stroke: '#dc2626',
      fill: '#fee2e2',
    };
  }

  if (normalized === 'skipped') {
    return {
      bg: '#f8fafc',
      border: '#cbd5e1',
      text: '#64748b',
      stroke: '#94a3b8',
      fill: '#e2e8f0',
    };
  }

  return {
    bg: '#f8fafc',
    border: '#cbd5e1',
    text: '#475569',
    stroke: '#94a3b8',
    fill: '#f1f5f9',
  };
}

function renderNodeStatusMarker(status: string | undefined) {
  const normalized = normalizeStatus(status);

  if (
    normalized === 'completed' ||
    normalized === 'success' ||
    normalized === 'succeeded'
  ) {
    return <path d="M -3.2 0 L -0.8 2.5 L 4 -3.2" fill="none" />;
  }

  if (
    normalized === 'failed' ||
    normalized === 'aborted' ||
    normalized === 'error'
  ) {
    return (
      <>
        <path d="M -3.2 -3.2 L 3.2 3.2" fill="none" />
        <path d="M 3.2 -3.2 L -3.2 3.2" fill="none" />
      </>
    );
  }

  if (normalized === 'running' || normalized === 'retry_scheduled') {
    return <path d="M -1.8 -3.2 L 4 0 L -1.8 3.2 Z" fill="#ffffff" />;
  }

  if (normalized === 'judging') {
    return <path d="M 0 -3.8 L 3.8 0 L 0 3.8 L -3.8 0 Z" fill="#ffffff" />;
  }

  if (normalized === 'skipped') {
    return <path d="M -3.6 0 L 3.6 0" fill="none" />;
  }

  return <circle cx="0" cy="0" fill="#ffffff" r="2" />;
}

function getDisplayAttempt(attempt?: number, maxAttempts?: number) {
  if (attempt === undefined) {
    return null;
  }

  const displayAttempt = attempt + 1;

  // Legacy runs stored attempt as 1-based, so cap the display value.
  if (
    maxAttempts !== undefined &&
    maxAttempts > 0 &&
    displayAttempt > maxAttempts
  ) {
    return maxAttempts;
  }

  return displayAttempt;
}

function formatNodeAttempt(
  status: string | undefined,
  attempt: number | undefined,
  maxAttempts?: number,
) {
  const normalizedStatus = normalizeStatus(status);

  if (normalizedStatus === 'pending' || normalizedStatus === 'ready') {
    return 'Not started';
  }

  if (normalizedStatus === 'skipped') {
    return 'Skipped';
  }

  const displayAttempt = getDisplayAttempt(attempt, maxAttempts);

  if (displayAttempt === null) {
    return '-';
  }

  const maxAttemptsText = maxAttempts === undefined ? '' : ` / ${maxAttempts}`;

  if (normalizedStatus === 'retry_scheduled') {
    return `Next ${displayAttempt}${maxAttemptsText}`;
  }

  return `${displayAttempt}${maxAttemptsText}`;
}

function formatJudgeAttempt(attempt?: number) {
  const displayAttempt = getDisplayAttempt(attempt);

  return displayAttempt === null ? '-' : String(displayAttempt);
}

function isRunActiveStatus(status?: string) {
  const normalized = normalizeStatus(status);

  if (RUN_TERMINAL_STATUSES.has(normalized)) {
    return false;
  }

  return RUN_ACTIVE_STATUSES.has(normalized);
}

function isNodeActiveStatus(status?: string) {
  return NODE_ACTIVE_STATUSES.has(normalizeStatus(status));
}

function isNodeTerminalStatus(status?: string) {
  return NODE_TERMINAL_STATUSES.has(normalizeStatus(status));
}

function isCompletedStatus(status?: string) {
  return normalizeStatus(status) === 'completed';
}

function isFailedStatus(status?: string) {
  const normalized = normalizeStatus(status);

  return normalized === 'failed' || normalized === 'aborted';
}

function isSkippedStatus(status?: string) {
  return normalizeStatus(status) === 'skipped';
}

function getEdgeState(
  edge: StateMachineEdge,
  nodeById: Map<string, StateMachineNode>,
): EdgeState {
  const sourceStatus = nodeById.get(edge.source)?.status;
  const targetStatus = nodeById.get(edge.target)?.status;

  if (isFailedStatus(sourceStatus)) {
    return 'blocked';
  }

  if (isSkippedStatus(sourceStatus) || isSkippedStatus(targetStatus)) {
    return 'skipped';
  }

  if (
    isCompletedStatus(targetStatus) ||
    isFailedStatus(targetStatus) ||
    normalizeStatus(targetStatus) === 'running' ||
    normalizeStatus(targetStatus) === 'judging' ||
    normalizeStatus(targetStatus) === 'retry_scheduled' ||
    normalizeStatus(targetStatus) === 'ready'
  ) {
    return 'executed';
  }

  return 'pending';
}

function isRetryableRequestStatus(status?: number) {
  return status === undefined || status >= 500;
}

class StateMachineRunRequestError extends Error {
  status?: number;

  retryable: boolean;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'StateMachineRunRequestError';
    this.status = status;
    this.retryable = isRetryableRequestStatus(status);
  }
}

function getBodyErrorMessage(body: unknown) {
  if (!body || typeof body !== 'object') {
    return '';
  }

  const payload = body as { error?: unknown; message?: unknown };
  const message = payload.message || payload.error;

  return typeof message === 'string' ? message : '';
}

async function parseErrorBody(response: Response) {
  const textResponse =
    typeof response.clone === 'function' ? response.clone() : response;

  try {
    const body = await response.json();
    return getBodyErrorMessage(body);
  } catch {
    try {
      const text = await textResponse.text();

      if (!text) {
        return '';
      }

      try {
        return getBodyErrorMessage(JSON.parse(text)) || text;
      } catch {
        return text;
      }
    } catch {
      return '';
    }
  }
}

async function createRequestError(response: Response) {
  const bodyMessage = await parseErrorBody(response);
  const fallback = `${response.status} ${response.statusText || ''}`.trim();

  return new StateMachineRunRequestError(
    bodyMessage || fallback || 'Failed to load graph',
    response.status,
  );
}

function normalizeRequestError(error: unknown) {
  if (error instanceof StateMachineRunRequestError) {
    return error;
  }

  if (error instanceof Error) {
    return new StateMachineRunRequestError(error.message);
  }

  return new StateMachineRunRequestError('Failed to load graph');
}

function resolveRunId(props: StateMachineRunViewProps) {
  return (
    props.runId ||
    props.stateMachineRunId ||
    props.smRunId ||
    props.data?.runId ||
    props.data?.stateMachineRunId ||
    props.data?.smRunId ||
    ''
  ).trim();
}

function resolveBaseUrl(props: StateMachineRunViewProps) {
  return (
    props.apiBaseUrl ||
    props.baseUrl ||
    props.data?.apiBaseUrl ||
    props.data?.baseUrl ||
    DEFAULT_BASE_URL
  );
}

function joinUrl(baseUrl: string, path: string) {
  const base = baseUrl.replace(/\/+$/, '');
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;

  return `${base}${normalizedPath}`;
}

function stringifyValue(value: unknown) {
  if (value === undefined || value === null || value === '') {
    return '-';
  }

  if (typeof value === 'string') {
    return value;
  }

  return JSON.stringify(value, null, 2);
}

async function copyTextToClipboard(text: string) {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  if (typeof document === 'undefined' || !document.body) {
    return;
  }

  const textArea = document.createElement('textarea');

  textArea.value = text;
  textArea.setAttribute('readonly', 'true');
  textArea.style.position = 'fixed';
  textArea.style.opacity = '0';
  document.body.appendChild(textArea);
  textArea.select();
  document.execCommand('copy');
  document.body.removeChild(textArea);
}

function isJsonObject(value: JsonValue | undefined): value is JsonObject {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value));
}

function readStringValue(source: JsonObject | null, key: string) {
  const value = source?.[key];

  return typeof value === 'string' ? value : '';
}

function readNumberValue(source: JsonObject | null, key: string) {
  const value = source?.[key];

  return typeof value === 'number' ? value : undefined;
}

function readBooleanValue(source: JsonObject | null, key: string) {
  const value = source?.[key];

  return typeof value === 'boolean' ? value : undefined;
}

function readObjectValue(source: JsonObject | null, key: string) {
  const value = source?.[key];

  return isJsonObject(value) ? value : null;
}

function readArrayValue(source: JsonObject | null, key: string) {
  const value = source?.[key];

  return Array.isArray(value) ? value : [];
}

function normalizeJudgeCriteria(
  criteria: JsonValue[],
): Array<{ criterion: string; evidence: string; satisfied?: boolean }> {
  const normalizedCriteria: Array<{
    criterion: string;
    evidence: string;
    satisfied?: boolean;
  }> = [];

  criteria.forEach((item) => {
    if (!isJsonObject(item)) {
      return;
    }

    const criterion = readStringValue(item, 'criterion');
    const evidence = readStringValue(item, 'evidence');
    const satisfied = readBooleanValue(item, 'satisfied');

    if (!criterion && !evidence) {
      return;
    }

    const normalizedItem: {
      criterion: string;
      evidence: string;
      satisfied?: boolean;
    } = {
      criterion: criterion || 'Unnamed criterion',
      evidence,
    };

    if (satisfied !== undefined) {
      normalizedItem.satisfied = satisfied;
    }

    normalizedCriteria.push(normalizedItem);
  });

  return normalizedCriteria;
}

function normalizeJudgeDecision(decision?: JsonValue) {
  const root = isJsonObject(decision) ? decision : null;
  const rawResponse = readObjectValue(root, 'raw_response');
  const criteriaSource = readArrayValue(root, 'checked_criteria').length
    ? readArrayValue(root, 'checked_criteria')
    : readArrayValue(rawResponse, 'checked_criteria');
  const checkedCriteria = normalizeJudgeCriteria(criteriaSource);
  const outcome =
    readStringValue(root, 'outcome') || readStringValue(rawResponse, 'outcome');
  const reason =
    readStringValue(root, 'reason') || readStringValue(rawResponse, 'reason');
  const confidence =
    readNumberValue(root, 'confidence') ??
    readNumberValue(rawResponse, 'confidence');
  const retryInstruction =
    readStringValue(root, 'retry_instruction') ||
    readStringValue(rawResponse, 'retry_instruction');

  if (!root) {
    return null;
  }

  return {
    checkedCriteria,
    confidence,
    outcome,
    reason,
    retryInstruction,
    raw: decision,
  };
}

function getJudgeOutcomeStatus(outcome?: string) {
  const normalized = normalizeStatus(outcome);

  if (
    normalized === 'approved' ||
    normalized === 'accepted' ||
    normalized === 'pass' ||
    normalized === 'passed'
  ) {
    return 'completed';
  }

  if (
    normalized === 'rejected' ||
    normalized === 'reject' ||
    normalized === 'retry' ||
    normalized === 'failed'
  ) {
    return 'failed';
  }

  return normalized;
}

function getRunLabel(graph: StateMachineRunGraph | null, fallback: string) {
  return graph?.definition.name || graph?.run.definition_id || fallback;
}

function getRunQuery(input?: JsonValue) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    return '';
  }

  const query = input.query;

  return typeof query === 'string' ? query.trim() : '';
}

function formatTime(value?: number) {
  if (!value) {
    return '-';
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return '-';
  }

  return date.toLocaleString();
}

function formatDuration(start?: number, end?: number) {
  if (!start) {
    return '-';
  }

  const finalEnd = end || Date.now();
  const duration = Math.max(0, finalEnd - start);

  if (duration < 1000) {
    return `${duration}ms`;
  }

  if (duration < 60_000) {
    return `${Math.round(duration / 1000)}s`;
  }

  return `${Math.round(duration / 60_000)}m`;
}

function formatDurationParts(start?: number, end?: number) {
  const durationText = formatDuration(start, end);
  const match = durationText.match(/^(\d+)(ms|s|m)$/);

  if (!match) {
    return { value: durationText, unit: '' };
  }

  const [, value, unit] = match;
  const displayUnit = unit === 'm' ? 'min' : unit === 's' ? 'sec' : 'ms';

  return { value, unit: displayUnit };
}

function formatMilliseconds(value?: number) {
  if (value === undefined || value === null) {
    return '-';
  }

  if (value < 1000) {
    return `${value}ms`;
  }

  if (value < 60_000) {
    return `${Math.round(value / 1000)}s`;
  }

  return `${Math.round(value / 60_000)}m`;
}

function buildGraphLayout(
  nodes: StateMachineNode[],
  edges: StateMachineEdge[],
  initialNodes: string[] = [],
): GraphLayout {
  const nodeById = new Map(nodes.map((node) => [node.node_id, node]));
  const incomingCount = new Map(nodes.map((node) => [node.node_id, 0]));

  edges.forEach((edge) => {
    incomingCount.set(edge.target, (incomingCount.get(edge.target) || 0) + 1);
  });

  const rootIds = initialNodes.filter((nodeId) => nodeById.has(nodeId));
  const fallbackRoots = nodes
    .filter((node) => (incomingCount.get(node.node_id) || 0) === 0)
    .map((node) => node.node_id);
  const startIds = rootIds.length > 0 ? rootIds : fallbackRoots;
  const levels = new Map(nodes.map((node) => [node.node_id, 0]));

  startIds.forEach((nodeId) => levels.set(nodeId, 0));

  for (let index = 0; index < nodes.length + edges.length; index += 1) {
    edges.forEach((edge) => {
      const sourceLevel = levels.get(edge.source);
      const targetLevel = levels.get(edge.target);

      if (sourceLevel === undefined || targetLevel === undefined) {
        return;
      }

      levels.set(edge.target, Math.max(targetLevel, sourceLevel + 1));
    });
  }

  const groups = new Map<number, StateMachineNode[]>();

  nodes.forEach((node) => {
    const level = levels.get(node.node_id) || 0;
    const group = groups.get(level) || [];

    group.push(node);
    groups.set(level, group);
  });

  const sortedGroups = Array.from(groups.entries()).sort(([a], [b]) => a - b);
  const maxColumns = Math.max(
    1,
    ...sortedGroups.map(([, group]) => group.length),
  );
  const width =
    PADDING * 2 +
    maxColumns * NODE_WIDTH +
    Math.max(0, maxColumns - 1) * COLUMN_GAP;
  const height =
    PADDING * 2 +
    sortedGroups.length * NODE_HEIGHT +
    Math.max(0, sortedGroups.length - 1) * LEVEL_GAP;
  const layoutNodes: LayoutNode[] = [];

  sortedGroups.forEach(([, group], levelIndex) => {
    const rowWidth =
      group.length * NODE_WIDTH + Math.max(0, group.length - 1) * COLUMN_GAP;
    const rowOffset = Math.max(0, (width - PADDING * 2 - rowWidth) / 2);
    const y = PADDING + levelIndex * (NODE_HEIGHT + LEVEL_GAP);

    group.forEach((node, row) => {
      layoutNodes.push({
        node,
        x: PADDING + rowOffset + row * (NODE_WIDTH + COLUMN_GAP),
        y,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      });
    });
  });

  const layoutById = new Map(
    layoutNodes.map((layoutNode) => [layoutNode.node.node_id, layoutNode]),
  );
  const layoutEdges = edges
    .map((edge) => {
      const source = layoutById.get(edge.source);
      const target = layoutById.get(edge.target);

      if (!source || !target) {
        return null;
      }

      return { edge, source, target };
    })
    .filter((edge): edge is NonNullable<typeof edge> => Boolean(edge));

  return {
    nodes: layoutNodes,
    edges: layoutEdges,
    width,
    height,
  };
}

function truncateText(text: string, maxLength: number) {
  if (text.length <= maxLength) {
    return text;
  }

  if (maxLength <= 3) {
    return text.slice(0, maxLength);
  }

  return `${text.slice(0, maxLength - 3)}...`;
}

function getNodeBotId(node: StateMachineNode) {
  return node.assignee_bot_id || '-';
}

function getNodeRole(node: StateMachineNode) {
  return node.assignee?.binding || '';
}

function CopyIcon({ size = 13 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
    >
      <path
        d="M8 8h10v12H8zM6 16H4V4h12v2"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function ClockIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
    >
      <circle cx="12" cy="12" r="8" stroke="currentColor" strokeWidth="2" />
      <path
        d="M12 7v5l3 2"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function AttemptIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
    >
      <path
        d="M7 7h8a4 4 0 0 1 0 8H9"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <path
        d="M10 4 7 7l3 3M14 20l3-3-3-3"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function DurationIcon({ size = 16 }: { size?: number }) {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height={size}
      viewBox="0 0 24 24"
      width={size}
    >
      <path
        d="M10 3h4M12 7v5l3 2"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
      <circle cx="12" cy="13" r="7" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

const StateMachineRunView: React.FC<StateMachineRunViewProps> = (props) => {
  const runId = resolveRunId(props);
  const baseUrl = resolveBaseUrl(props);
  const pollingInterval = props.pollingInterval || DEFAULT_POLLING_INTERVAL;
  const autoRefresh = props.autoRefresh !== false;
  const [graph, setGraph] = useState<StateMachineRunGraph | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nodeDetail, setNodeDetail] =
    useState<StateMachineNodeDetailResponse | null>(null);
  const [nodeDetailLoading, setNodeDetailLoading] = useState(false);
  const [nodeDetailError, setNodeDetailError] = useState<string | null>(null);
  const [nodeDetailModalOpen, setNodeDetailModalOpen] = useState(false);
  const [nodeArtifactExpanded, setNodeArtifactExpanded] = useState(true);
  const [judgeOutputsExpanded, setJudgeOutputsExpanded] = useState(false);
  const [copiedTarget, setCopiedTarget] = useState<CopyTarget>(null);
  const abortRef = useRef<AbortController | null>(null);
  const nodeDetailAbortRef = useRef<AbortController | null>(null);
  const nodeDetailPollInFlightRef = useRef(false);
  const requestedNodeDetailIdRef = useRef<string | null>(null);
  const graphRef = useRef<StateMachineRunGraph | null>(null);
  const transientRetryCountRef = useRef(0);
  const copyResetTimerRef = useRef<number | null>(null);
  const [transientRetrySignal, setTransientRetrySignal] = useState(0);

  const fetchGraph = useCallback(
    async (mode: 'initial' | 'refresh' = 'initial') => {
      if (!runId) {
        return;
      }

      abortRef.current?.abort();

      const abortController = new AbortController();
      abortRef.current = abortController;

      if (mode === 'initial') {
        setLoading(true);
      } else {
        setRefreshing(true);
      }

      setError(null);

      try {
        const response = await fetch(
          joinUrl(
            baseUrl,
            `/state-machine-runs/${encodeURIComponent(runId)}/graph`,
          ),
          {
            credentials: 'include',
            signal: abortController.signal,
          },
        );

        if (!response.ok) {
          throw await createRequestError(response);
        }

        const data = (await response.json()) as StateMachineRunGraph;
        transientRetryCountRef.current = 0;
        setTransientRetrySignal(0);
        setGraph(data);
        setSelectedNodeId((current) => {
          if (current && data.nodes.some((node) => node.node_id === current)) {
            return current;
          }

          const runningNode = data.nodes.find(
            (node) => normalizeStatus(node.status) === 'running',
          );
          const judgingNode = data.nodes.find(
            (node) => normalizeStatus(node.status) === 'judging',
          );
          const activeNode = data.nodes.find((node) =>
            isNodeActiveStatus(node.status),
          );

          return (
            runningNode?.node_id ||
            judgingNode?.node_id ||
            activeNode?.node_id ||
            data.nodes[0]?.node_id ||
            null
          );
        });
      } catch (requestError) {
        if ((requestError as Error).name !== 'AbortError') {
          const normalizedError = normalizeRequestError(requestError);
          const shouldRetry =
            normalizedError.retryable &&
            transientRetryCountRef.current < MAX_TRANSIENT_RETRIES &&
            (!graphRef.current ||
              isRunActiveStatus(graphRef.current.run.status));

          if (shouldRetry) {
            transientRetryCountRef.current += 1;
            setTransientRetrySignal((signal) => signal + 1);
          }

          setError(normalizedError.message || 'Failed to load graph');
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [baseUrl, runId],
  );

  const fetchNodeDetail = useCallback(
    async (nodeId: string, options: { keepPrevious?: boolean } = {}) => {
      if (!runId || !nodeId) {
        return;
      }

      const keepPrevious = options.keepPrevious === true;

      nodeDetailAbortRef.current?.abort();

      const abortController = new AbortController();
      nodeDetailAbortRef.current = abortController;
      requestedNodeDetailIdRef.current = nodeId;

      if (!keepPrevious) {
        setNodeDetail(null);
        setNodeDetailLoading(true);
      }
      setNodeDetailError(null);

      try {
        const response = await fetch(
          joinUrl(
            baseUrl,
            `/state-machine-runs/${encodeURIComponent(
              runId,
            )}/nodes/${encodeURIComponent(nodeId)}`,
          ),
          {
            credentials: 'include',
            signal: abortController.signal,
          },
        );

        if (!response.ok) {
          throw await createRequestError(response);
        }

        const data = (await response.json()) as StateMachineNodeDetailResponse;

        if (nodeDetailAbortRef.current === abortController) {
          setNodeDetail(data);
        }
      } catch (requestError) {
        if ((requestError as Error).name !== 'AbortError') {
          const normalizedError = normalizeRequestError(requestError);

          if (nodeDetailAbortRef.current === abortController) {
            setNodeDetailError(
              normalizedError.message || 'Failed to load node detail',
            );
          }
        }
      } finally {
        if (!keepPrevious && nodeDetailAbortRef.current === abortController) {
          setNodeDetailLoading(false);
        }
      }
    },
    [baseUrl, runId],
  );

  useEffect(() => {
    setGraph(null);
    setSelectedNodeId(null);
    setError(null);
    setNodeDetail(null);
    setNodeDetailError(null);
    setNodeDetailLoading(false);
    setNodeDetailModalOpen(false);
    setNodeArtifactExpanded(true);
    setJudgeOutputsExpanded(false);
    setCopiedTarget(null);
    nodeDetailAbortRef.current?.abort();
    nodeDetailAbortRef.current = null;
    nodeDetailPollInFlightRef.current = false;
    requestedNodeDetailIdRef.current = null;
    if (copyResetTimerRef.current) {
      window.clearTimeout(copyResetTimerRef.current);
      copyResetTimerRef.current = null;
    }
    graphRef.current = null;
    transientRetryCountRef.current = 0;
    setTransientRetrySignal(0);
  }, [runId, baseUrl]);

  useEffect(() => {
    graphRef.current = graph;
  }, [graph]);

  useEffect(() => {
    if (
      !runId ||
      !selectedNodeId ||
      !nodeDetailModalOpen ||
      nodeDetailLoading
    ) {
      return;
    }

    if (nodeDetail?.node?.node_id === selectedNodeId) {
      return;
    }

    if (requestedNodeDetailIdRef.current === selectedNodeId) {
      return;
    }

    fetchNodeDetail(selectedNodeId);
  }, [
    fetchNodeDetail,
    nodeDetail?.node?.node_id,
    nodeDetailLoading,
    nodeDetailModalOpen,
    runId,
    selectedNodeId,
  ]);

  useEffect(() => {
    fetchGraph('initial');

    return () => {
      abortRef.current?.abort();
      nodeDetailAbortRef.current?.abort();
      nodeDetailAbortRef.current = null;
      if (copyResetTimerRef.current) {
        window.clearTimeout(copyResetTimerRef.current);
        copyResetTimerRef.current = null;
      }
    };
  }, [fetchGraph]);

  useEffect(() => {
    const shouldPollActiveRun = graph && isRunActiveStatus(graph.run.status);
    const shouldRetryTransientError = !graph && transientRetrySignal > 0;

    if (!autoRefresh || (!shouldPollActiveRun && !shouldRetryTransientError)) {
      return undefined;
    }

    const retryCount = transientRetryCountRef.current;
    const retryDelay =
      retryCount > 0
        ? Math.min(
            pollingInterval * Math.pow(2, retryCount - 1),
            DEFAULT_POLLING_INTERVAL * 10,
          )
        : pollingInterval;

    const timer = window.setTimeout(() => {
      fetchGraph(graph ? 'refresh' : 'initial');
    }, retryDelay);

    return () => window.clearTimeout(timer);
  }, [autoRefresh, fetchGraph, graph, pollingInterval, transientRetrySignal]);

  const layout = useMemo(() => {
    if (!graph) {
      return null;
    }

    return buildGraphLayout(
      graph.nodes,
      graph.edges,
      graph.definition.initial_nodes,
    );
  }, [graph]);

  const selectedNode = useMemo(() => {
    if (!graph || !selectedNodeId) {
      return null;
    }

    return graph.nodes.find((node) => node.node_id === selectedNodeId) || null;
  }, [graph, selectedNodeId]);

  const nodeById = useMemo(() => {
    return new Map(graph?.nodes.map((node) => [node.node_id, node]) || []);
  }, [graph]);

  const selectedNodeDetail = useMemo(() => {
    if (!selectedNodeId || nodeDetail?.node?.node_id !== selectedNodeId) {
      return null;
    }

    return nodeDetail;
  }, [nodeDetail, selectedNodeId]);
  const judgeOutputSummary = useMemo(() => {
    const judgeOutputs = selectedNodeDetail?.judge_outputs || [];

    if (!judgeOutputs.length) {
      return null;
    }

    const latestJudgeOutput = judgeOutputs.reduce((latest, judgeOutput) => {
      const latestTime = latest.created_at ?? Number.NEGATIVE_INFINITY;
      const judgeTime = judgeOutput.created_at ?? Number.NEGATIVE_INFINITY;

      return judgeTime >= latestTime ? judgeOutput : latest;
    }, judgeOutputs[0]);
    const decision = normalizeJudgeDecision(latestJudgeOutput.decision);
    const outcome = decision?.outcome || 'unknown';
    const outcomeStatus = getJudgeOutcomeStatus(outcome);

    return {
      outcome,
      tone: getStatusTone(outcomeStatus),
    };
  }, [selectedNodeDetail]);

  const selectedRuntimeNode = selectedNodeDetail?.node;
  const runLabel = getRunLabel(graph, runId);
  const runQuery = getRunQuery(graph?.run.input);
  const headerTitle = graph ? runQuery || runLabel : runId;
  const selectedNodeStatus =
    selectedRuntimeNode?.status || selectedNode?.status || undefined;
  const selectedNodeAttempt =
    selectedRuntimeNode?.attempt ?? selectedNode?.attempt;
  const selectedNodeAttemptText = formatNodeAttempt(
    selectedNodeStatus,
    selectedNodeAttempt,
    selectedRuntimeNode?.max_attempts,
  );
  const selectedNodeStarted =
    selectedRuntimeNode?.started_at || selectedNode?.started_at;
  const selectedNodeCompleted =
    selectedRuntimeNode?.completed_at || selectedNode?.completed_at;
  const selectedNodeAssignee = selectedNode
    ? selectedNode.assignee?.binding ||
      selectedRuntimeNode?.assignee_bot_id ||
      selectedNode.assignee_bot_id ||
      stringifyValue(selectedNode.assignee)
    : '-';
  const selectedNodeStatusTone = getStatusTone(selectedNodeStatus);
  const selectedNodeDurationParts = formatDurationParts(
    selectedNodeStarted,
    selectedNodeCompleted,
  );

  useEffect(() => {
    if (
      !autoRefresh ||
      !runId ||
      !selectedNodeId ||
      !nodeDetailModalOpen ||
      nodeDetailLoading ||
      !['running', 'judging'].includes(normalizeStatus(selectedNodeStatus))
    ) {
      return undefined;
    }

    const timer = window.setInterval(() => {
      if (nodeDetailPollInFlightRef.current) {
        return;
      }

      nodeDetailPollInFlightRef.current = true;
      fetchNodeDetail(selectedNodeId, { keepPrevious: true }).finally(() => {
        nodeDetailPollInFlightRef.current = false;
      });
    }, pollingInterval);

    return () => {
      window.clearInterval(timer);
      nodeDetailPollInFlightRef.current = false;
    };
  }, [
    autoRefresh,
    fetchNodeDetail,
    nodeDetailLoading,
    nodeDetailModalOpen,
    pollingInterval,
    runId,
    selectedNodeId,
    selectedNodeStatus,
  ]);

  const taskOutputPlaceholder =
    graph && isRunActiveStatus(graph.run.status)
      ? 'Task is still running. Output will appear after the run completes.'
      : 'No task output returned.';
  const taskOutputText =
    graph?.run.output !== undefined ? stringifyValue(graph.run.output) : '';
  const nodeArtifactText = selectedRuntimeNode?.artifact_text || '';

  const handleCopyText = useCallback(
    async (text: string, target: CopyTarget) => {
      if (!text || !target) {
        return;
      }

      await copyTextToClipboard(text);
      setCopiedTarget(target);

      if (copyResetTimerRef.current) {
        window.clearTimeout(copyResetTimerRef.current);
      }

      copyResetTimerRef.current = window.setTimeout(() => {
        setCopiedTarget(null);
        copyResetTimerRef.current = null;
      }, 1400);
    },
    [],
  );

  const handleCopyRunId = useCallback(() => {
    void handleCopyText(runId, 'run-id');
  }, [handleCopyText, runId]);

  const handleCopyTaskOutput = useCallback(() => {
    void handleCopyText(taskOutputText, 'task-output');
  }, [handleCopyText, taskOutputText]);

  const handleCopyNodeArtifact = useCallback(() => {
    void handleCopyText(nodeArtifactText, 'node-artifact');
  }, [handleCopyText, nodeArtifactText]);

  const handleCloseNodeDetail = useCallback(() => {
    setNodeDetailModalOpen(false);
  }, []);

  const nodeDetailContent = selectedNode ? (
    <>
      <DetailSection>
        <NodeDetailHero>
          <NodeTagRow>
            <NodeTag $tone="type" title={selectedNode.kind || 'node'}>
              <NodeTagLabel $tone="type">TYPE</NodeTagLabel>
              <NodeTagValue $tone="type">
                {selectedNode.kind || 'node'}
              </NodeTagValue>
            </NodeTag>
            <NodeTag $tone="role" title={selectedNodeAssignee}>
              <NodeTagLabel $tone="role">ROLE</NodeTagLabel>
              <NodeTagValue $tone="role">{selectedNodeAssignee}</NodeTagValue>
            </NodeTag>
            <NodeSummaryStatus>
              <NodeStatusSummaryPill $tone={selectedNodeStatusTone}>
                <NodeStatusDot $tone={selectedNodeStatusTone} />
                {selectedNodeStatus || 'unknown'}
              </NodeStatusSummaryPill>
            </NodeSummaryStatus>
          </NodeTagRow>

          <NodeTimeGrid>
            <NodeInfoCard>
              <NodeInfoIcon $tone="time">
                <ClockIcon />
              </NodeInfoIcon>
              <NodeInfoValue title={formatTime(selectedNodeStarted)}>
                {formatTime(selectedNodeStarted)}
              </NodeInfoValue>
            </NodeInfoCard>
            <NodeSummaryArrow>
              <svg
                aria-hidden="true"
                fill="none"
                height="16"
                viewBox="0 0 24 24"
                width="16"
              >
                <path
                  d="M5 12h14M13 6l6 6-6 6"
                  stroke="currentColor"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth="2"
                />
              </svg>
            </NodeSummaryArrow>
            <NodeInfoCard>
              <NodeInfoIcon $tone="time">
                <ClockIcon />
              </NodeInfoIcon>
              <NodeInfoValue title={formatTime(selectedNodeCompleted)}>
                {formatTime(selectedNodeCompleted)}
              </NodeInfoValue>
            </NodeInfoCard>
          </NodeTimeGrid>

          <NodeMetricGrid>
            <NodeInfoCard>
              <NodeInfoIcon $tone="attempt">
                <AttemptIcon />
              </NodeInfoIcon>
              <NodeInfoStack>
                <NodeInfoLabel>Attempt</NodeInfoLabel>
                <NodeInfoValue>{selectedNodeAttemptText}</NodeInfoValue>
              </NodeInfoStack>
            </NodeInfoCard>
            <NodeInfoCard>
              <NodeInfoIcon $tone="duration">
                <DurationIcon />
              </NodeInfoIcon>
              <NodeInfoStack>
                <NodeInfoLabel>Duration</NodeInfoLabel>
                <NodeMetricValue>
                  <NodeInfoValue>
                    {selectedNodeDurationParts.value}
                  </NodeInfoValue>
                  {selectedNodeDurationParts.unit ? (
                    <NodeMetricUnit>
                      {selectedNodeDurationParts.unit}
                    </NodeMetricUnit>
                  ) : null}
                </NodeMetricValue>
              </NodeInfoStack>
            </NodeInfoCard>
          </NodeMetricGrid>

          {selectedNode.final_output ? (
            <FinalOutputNote>Final output node</FinalOutputNote>
          ) : null}
        </NodeDetailHero>

        {selectedRuntimeNode?.error ? (
          <InlineNotice $danger>
            <strong>Node error:</strong> {selectedRuntimeNode.error}
          </InlineNotice>
        ) : null}

        {nodeDetailError ? (
          <InlineNotice $danger>{nodeDetailError}</InlineNotice>
        ) : null}
      </DetailSection>

      {selectedRuntimeNode?.artifact_text ? (
        <DetailSection>
          <CollapsibleHeader>
            <CollapsibleToggle
              aria-controls="state-machine-node-output"
              aria-expanded={nodeArtifactExpanded}
              type="button"
              onClick={() => setNodeArtifactExpanded((expanded) => !expanded)}
            >
              <ToggleIcon $expanded={nodeArtifactExpanded}>
                <svg
                  aria-hidden="true"
                  fill="none"
                  height="14"
                  viewBox="0 0 24 24"
                  width="14"
                >
                  <path
                    d="M9 6l6 6-6 6"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2.4"
                  />
                </svg>
              </ToggleIcon>
              <CollapsibleTitle>Node output</CollapsibleTitle>
            </CollapsibleToggle>
            {nodeArtifactExpanded ? (
              <CopyButton
                aria-label="Copy node output"
                type="button"
                onClick={handleCopyNodeArtifact}
              >
                <CopyIcon />
                {copiedTarget === 'node-artifact' ? 'Copied' : 'Copy'}
              </CopyButton>
            ) : null}
          </CollapsibleHeader>
          {nodeArtifactExpanded ? (
            <OutputBlock id="state-machine-node-output">
              {selectedRuntimeNode.artifact_text}
            </OutputBlock>
          ) : null}
        </DetailSection>
      ) : null}

      {selectedNodeDetail?.judge_outputs?.length ? (
        <DetailSection>
          <CollapsibleHeader>
            <CollapsibleToggle
              aria-controls="state-machine-judge-outputs"
              aria-expanded={judgeOutputsExpanded}
              type="button"
              onClick={() => setJudgeOutputsExpanded((expanded) => !expanded)}
            >
              <ToggleIcon $expanded={judgeOutputsExpanded}>
                <svg
                  aria-hidden="true"
                  fill="none"
                  height="14"
                  viewBox="0 0 24 24"
                  width="14"
                >
                  <path
                    d="M9 6l6 6-6 6"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2.4"
                  />
                </svg>
              </ToggleIcon>
              <CollapsibleTitle>Judge outputs</CollapsibleTitle>
            </CollapsibleToggle>
            {!judgeOutputsExpanded && judgeOutputSummary ? (
              <JudgeStatusPill
                $tone={judgeOutputSummary.tone}
                title={`Latest judge result: ${judgeOutputSummary.outcome}`}
              >
                <JudgeStatusDot $tone={judgeOutputSummary.tone} />
                {judgeOutputSummary.outcome}
              </JudgeStatusPill>
            ) : null}
          </CollapsibleHeader>
          {judgeOutputsExpanded ? (
            <JudgeOutputList id="state-machine-judge-outputs">
              {selectedNodeDetail.judge_outputs.map((judgeOutput, index) => {
                const decision = normalizeJudgeDecision(judgeOutput.decision);
                const outcome = decision?.outcome || 'unknown';
                const outcomeStatus = getJudgeOutcomeStatus(outcome);
                const outcomeTone = getStatusTone(outcomeStatus);

                return (
                  <JudgeOutputCard
                    key={`${judgeOutput.node_id}-${
                      judgeOutput.attempt ?? index
                    }-${judgeOutput.created_at ?? index}`}
                  >
                    <JudgeMetaRow>
                      <JudgeMetaText>
                        Attempt
                        <JudgeMetaValue>
                          {formatJudgeAttempt(judgeOutput.attempt)}
                        </JudgeMetaValue>
                        <JudgeMetaSeparator>·</JudgeMetaSeparator>
                        <JudgeMetaValue>
                          {formatTime(judgeOutput.created_at)}
                        </JudgeMetaValue>
                      </JudgeMetaText>
                      <JudgeStatusPill $tone={outcomeTone}>
                        <JudgeStatusDot $tone={outcomeTone} />
                        {outcome}
                      </JudgeStatusPill>
                    </JudgeMetaRow>

                    {decision ? (
                      <>
                        <DecisionSummary>
                          <DecisionHeader>
                            <DecisionTitle>Final decision</DecisionTitle>
                            {decision.confidence !== undefined ? (
                              <ConfidenceBadge>
                                Confidence{' '}
                                {Math.round(decision.confidence * 100)}%
                              </ConfidenceBadge>
                            ) : null}
                          </DecisionHeader>
                          <DecisionReason>
                            {decision.reason || 'No decision reason returned.'}
                          </DecisionReason>
                          {decision.retryInstruction ? (
                            <RetryInstruction>
                              <RetryIcon>
                                <AttemptIcon size={13} />
                              </RetryIcon>
                              <div>
                                <RetryTitle>Retry instruction</RetryTitle>
                                <RetryText>
                                  {decision.retryInstruction}
                                </RetryText>
                              </div>
                            </RetryInstruction>
                          ) : null}
                        </DecisionSummary>

                        {decision.checkedCriteria.length ? (
                          <>
                            <CriteriaHeader>Checked criteria</CriteriaHeader>
                            <CriteriaList>
                              {decision.checkedCriteria.map(
                                (criteria, criteriaIndex) => (
                                  <CriteriaItem
                                    $satisfied={criteria.satisfied}
                                    key={`${criteria.criterion}-${criteriaIndex}`}
                                  >
                                    <CriteriaMarker
                                      $satisfied={criteria.satisfied}
                                    >
                                      {criteria.satisfied === undefined
                                        ? '?'
                                        : criteria.satisfied
                                        ? 'OK'
                                        : 'NO'}
                                    </CriteriaMarker>
                                    <div>
                                      <CriteriaTitle>
                                        {criteria.criterion}
                                      </CriteriaTitle>
                                      {criteria.evidence ? (
                                        <CriteriaEvidence>
                                          {criteria.evidence}
                                        </CriteriaEvidence>
                                      ) : null}
                                    </div>
                                  </CriteriaItem>
                                ),
                              )}
                            </CriteriaList>
                          </>
                        ) : null}
                      </>
                    ) : (
                      <OutputBlock>
                        {stringifyValue(judgeOutput.decision)}
                      </OutputBlock>
                    )}
                  </JudgeOutputCard>
                );
              })}
            </JudgeOutputList>
          ) : null}
        </DetailSection>
      ) : null}

      <DetailSection>
        <RuntimeRows>
          <RuntimeRow>
            <RuntimeLabel>Bot id</RuntimeLabel>
            <RuntimeStrongValue>
              {selectedRuntimeNode?.assignee_bot_id ||
                selectedNode.assignee_bot_id ||
                '-'}
            </RuntimeStrongValue>
          </RuntimeRow>
          <RuntimeRow>
            <RuntimeLabel>Timeout</RuntimeLabel>
            <RuntimeValue>
              {formatMilliseconds(selectedRuntimeNode?.node_timeout_ms)}
            </RuntimeValue>
          </RuntimeRow>
          <RuntimeRow>
            <RuntimeLabel>Delivery</RuntimeLabel>
            <RuntimeStrongValue>
              {selectedRuntimeNode?.delivery_request_id || '-'}
            </RuntimeStrongValue>
          </RuntimeRow>
          <RuntimeRow>
            <RuntimeLabel>Bot run</RuntimeLabel>
            <RuntimeStrongValue>
              {selectedRuntimeNode?.bot_delivery_run_id || '-'}
            </RuntimeStrongValue>
          </RuntimeRow>
        </RuntimeRows>
      </DetailSection>
    </>
  ) : null;

  if (!runId) {
    return (
      <Container className={props.className} style={props.style}>
        <Message>Missing state machine run id.</Message>
      </Container>
    );
  }

  return (
    <Container className={props.className} style={props.style}>
      <Header>
        <HeaderTop>
          <TitleGroup>
            {graph ? <Eyebrow>{runLabel}</Eyebrow> : null}
            <Title>{headerTitle}</Title>
          </TitleGroup>
          <Actions>
            {graph?.run.status ? (
              <StatusPill $tone={getStatusTone(graph.run.status)}>
                {graph.run.status}
              </StatusPill>
            ) : null}
            <IconButton
              aria-label="Refresh state machine run"
              disabled={loading || refreshing}
              title="Refresh"
              type="button"
              onClick={() => fetchGraph('refresh')}
            >
              <svg
                aria-hidden="true"
                fill="none"
                height="16"
                viewBox="0 0 24 24"
                width="16"
              >
                <path
                  d="M20 11a8 8 0 1 0-2.34 5.66M20 11V5m0 6h-6"
                  stroke="currentColor"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth="2"
                />
              </svg>
            </IconButton>
          </Actions>
        </HeaderTop>

        <HeaderInfo>
          <SubTitle>
            <SubTitleText>{runId}</SubTitleText>
            <InlineCopyButton
              aria-label="Copy run id"
              type="button"
              onClick={handleCopyRunId}
            >
              <CopyIcon size={11} />
              {copiedTarget === 'run-id' ? 'Copied' : 'Copy'}
            </InlineCopyButton>
          </SubTitle>
          {graph ? (
            <HeaderMetaRow>
              <HeaderMetaItem>
                <HeaderMetaLabel>Started</HeaderMetaLabel>
                <HeaderMetaValue title={formatTime(graph.run.created_at)}>
                  {formatTime(graph.run.created_at)}
                </HeaderMetaValue>
              </HeaderMetaItem>
              <HeaderMetaItem>
                <HeaderMetaLabel>Updated</HeaderMetaLabel>
                <HeaderMetaValue title={formatTime(graph.run.updated_at)}>
                  {formatTime(graph.run.updated_at)}
                </HeaderMetaValue>
              </HeaderMetaItem>
            </HeaderMetaRow>
          ) : null}
        </HeaderInfo>
      </Header>

      <ScrollArea>
        {loading && <LoadingBar />}

        {error ? <ErrorMessage>{error}</ErrorMessage> : null}

        {!graph && loading ? <Message>Loading graph...</Message> : null}

        {graph ? (
          <>
            <ContentGrid>
              <GraphShell>
                {layout && layout.nodes.length > 0 ? (
                  <GraphSvg
                    aria-label="State machine graph"
                    height={layout.height}
                    role="img"
                    viewBox={`0 0 ${layout.width} ${layout.height}`}
                    width={layout.width}
                  >
                    <defs>
                      <filter
                        id="sm-node-shadow"
                        x="-8%"
                        y="-8%"
                        width="116%"
                        height="132%"
                      >
                        <feDropShadow
                          dx="0"
                          dy="2"
                          stdDeviation="3"
                          floodColor="rgba(15,23,42,0.06)"
                          floodOpacity="1"
                        />
                      </filter>
                      <filter
                        id="sm-node-shadow-selected"
                        x="-8%"
                        y="-8%"
                        width="116%"
                        height="132%"
                      >
                        <feDropShadow
                          dx="0"
                          dy="3"
                          stdDeviation="5"
                          floodColor="rgba(37,99,235,0.15)"
                          floodOpacity="1"
                        />
                      </filter>
                      {Object.entries(EDGE_TONES).map(([state, tone]) => (
                        <marker
                          key={state}
                          id={`sm-arrow-${state}`}
                          markerHeight="8"
                          markerWidth="8"
                          orient="auto"
                          refX="7"
                          refY="4"
                          viewBox="0 0 8 8"
                        >
                          <path d="M0,0 L8,4 L0,8 Z" fill={tone.marker} />
                        </marker>
                      ))}
                    </defs>

                    {layout.edges
                      .map((layoutEdge, index) => {
                        const edgeState = getEdgeState(
                          layoutEdge.edge,
                          nodeById,
                        );

                        return {
                          ...layoutEdge,
                          edgeState,
                          index,
                        };
                      })
                      .sort(
                        (a, b) =>
                          EDGE_RENDER_ORDER[a.edgeState] -
                            EDGE_RENDER_ORDER[b.edgeState] || a.index - b.index,
                      )
                      .map(({ edge, source, target, edgeState }) => {
                        const edgeTone = EDGE_TONES[edgeState];
                        const sourceX = source.x + source.width / 2;
                        const sourceY = source.y + source.height;
                        const targetX = target.x + target.width / 2;
                        const targetY = target.y;
                        const midY = sourceY + (targetY - sourceY) / 2;
                        const isStraight = sourceX === targetX;
                        const path = isStraight
                          ? `M ${sourceX} ${sourceY} L ${targetX} ${targetY}`
                          : `M ${sourceX} ${sourceY} C ${sourceX} ${midY}, ${targetX} ${midY}, ${targetX} ${targetY}`;

                        return (
                          <g
                            key={`${edge.source}-${edge.outcome}-${edge.target}`}
                          >
                            <path
                              data-edge-outcome={edge.outcome || undefined}
                              data-edge-source={edge.source}
                              data-edge-state={edgeState}
                              data-edge-target={edge.target}
                              d={path}
                              fill="none"
                              markerEnd={`url(#sm-arrow-${edgeState})`}
                              stroke={edgeTone.stroke}
                              strokeDasharray={edgeTone.strokeDasharray}
                              strokeWidth={edgeTone.strokeWidth}
                            />
                            {edge.outcome ? (
                              <text
                                fill={edgeTone.label}
                                fontSize="12"
                                fontWeight={
                                  edgeState === 'executed' ||
                                  edgeState === 'blocked'
                                    ? 650
                                    : 500
                                }
                                textAnchor="middle"
                                x={(sourceX + targetX) / 2}
                                y={midY - 6}
                              >
                                {edge.outcome}
                              </text>
                            ) : null}
                          </g>
                        );
                      })}

                    {layout.nodes.map((layoutNode) => {
                      const { node, x, y, width, height } = layoutNode;
                      const tone = getStatusTone(node.status);
                      const selected = node.node_id === selectedNodeId;
                      const nodePhase = isNodeActiveStatus(node.status)
                        ? 'active'
                        : isNodeTerminalStatus(node.status)
                        ? 'terminal'
                        : 'unknown';
                      const botId = getNodeBotId(node);
                      const role = getNodeRole(node);

                      return (
                        <NodeGroup
                          key={node.node_id}
                          aria-label={`Node ${
                            node.display_name || node.node_id
                          } ${nodePhase}`}
                          onClick={() => {
                            requestedNodeDetailIdRef.current = null;
                            setSelectedNodeId(node.node_id);
                            setNodeArtifactExpanded(true);
                            setJudgeOutputsExpanded(false);
                            setNodeDetailModalOpen(true);
                            props.onInteraction?.({
                              type: 'select-node',
                              node,
                              run: graph.run,
                            });
                          }}
                          onKeyDown={(event) => {
                            if (event.key !== 'Enter' && event.key !== ' ') {
                              return;
                            }

                            event.preventDefault();
                            requestedNodeDetailIdRef.current = null;
                            setSelectedNodeId(node.node_id);
                            setNodeArtifactExpanded(true);
                            setJudgeOutputsExpanded(false);
                            setNodeDetailModalOpen(true);
                          }}
                          role="button"
                          tabIndex={0}
                        >
                          <rect
                            fill={
                              nodePhase !== 'unknown'
                                ? tone.fill
                                : selected
                                ? '#f8fbff'
                                : '#ffffff'
                            }
                            filter={
                              selected
                                ? 'url(#sm-node-shadow-selected)'
                                : 'url(#sm-node-shadow)'
                            }
                            height={height}
                            rx="10"
                            stroke={tone.stroke}
                            strokeWidth={selected ? 2 : 1.2}
                            width={width}
                            x={x}
                            y={y}
                            vectorEffect="non-scaling-stroke"
                          />
                          {['running', 'judging'].includes(
                            normalizeStatus(node.status),
                          ) ? (
                            <rect
                              fill="none"
                              height={height}
                              rx="10"
                              stroke={tone.stroke}
                              strokeOpacity="0.4"
                              strokeWidth="2.5"
                              width={width}
                              x={x}
                              y={y}
                              vectorEffect="non-scaling-stroke"
                            >
                              <animate
                                attributeName="stroke-opacity"
                                dur="2s"
                                repeatCount="indefinite"
                                values="0.4;0.1;0.4"
                              />
                              <animate
                                attributeName="stroke-width"
                                dur="2s"
                                repeatCount="indefinite"
                                values="2.5;4;2.5"
                              />
                            </rect>
                          ) : null}
                          <text
                            fill="#0f172a"
                            fontSize="13"
                            fontWeight="650"
                            textAnchor="middle"
                            x={x + width / 2}
                            y={y + 22}
                          >
                            {truncateText(
                              node.display_name || node.node_id,
                              22,
                            )}
                          </text>
                          <g
                            stroke="#ffffff"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth="1.8"
                            transform={`translate(${x + width - 1} ${y + 1})`}
                          >
                            <title>{node.status || 'unknown'}</title>
                            <circle
                              cx="0"
                              cy="0"
                              fill={tone.stroke}
                              r="7"
                              stroke="#ffffff"
                              strokeWidth="1.5"
                            />
                            {renderNodeStatusMarker(node.status)}
                          </g>
                          <foreignObject
                            height="18"
                            width={width - 24}
                            x={x + 12}
                            y={y + 34}
                          >
                            <NodeMetaRow>
                              <BotIdText title={botId}>
                                {truncateText(botId, 20)}
                              </BotIdText>
                              {role ? (
                                <RoleTag title={role}>{role}</RoleTag>
                              ) : null}
                            </NodeMetaRow>
                          </foreignObject>
                          {node.final_output && !role ? (
                            <text
                              fill="#7c3aed"
                              fontSize="11"
                              fontWeight="700"
                              textAnchor="end"
                              x={x + width - 12}
                              y={y + 47}
                            >
                              final
                            </text>
                          ) : null}
                        </NodeGroup>
                      );
                    })}
                  </GraphSvg>
                ) : (
                  <Message>No graph nodes.</Message>
                )}
              </GraphShell>

              <Panel>
                <DetailSection>
                  <SectionHeader>
                    <SectionTitle>Task output</SectionTitle>
                    {graph.run.output !== undefined ? (
                      <CopyButton
                        aria-label="Copy task output"
                        type="button"
                        onClick={handleCopyTaskOutput}
                      >
                        <CopyIcon />
                        {copiedTarget === 'task-output' ? 'Copied' : 'Copy'}
                      </CopyButton>
                    ) : null}
                  </SectionHeader>
                  {graph.run.output !== undefined ? (
                    <OutputBlock>{taskOutputText}</OutputBlock>
                  ) : (
                    <OutputPlaceholder>
                      {taskOutputPlaceholder}
                    </OutputPlaceholder>
                  )}
                </DetailSection>

                <DetailSection>
                  <RuntimeRows>
                    <RuntimeRow>
                      <RuntimeLabel>Definition</RuntimeLabel>
                      <RuntimeStrongValue>
                        {graph.run.definition_id}@{graph.run.definition_version}
                      </RuntimeStrongValue>
                    </RuntimeRow>
                    <RuntimeRow>
                      <RuntimeLabel>Graph</RuntimeLabel>
                      <RuntimeValue>
                        {graph.nodes.length} nodes / {graph.edges.length} edges
                      </RuntimeValue>
                    </RuntimeRow>
                  </RuntimeRows>
                </DetailSection>
              </Panel>
            </ContentGrid>
          </>
        ) : null}
      </ScrollArea>

      {nodeDetailModalOpen && selectedNode ? (
        <ModalOverlay
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              handleCloseNodeDetail();
            }
          }}
        >
          <ModalDialog
            aria-labelledby="state-machine-node-detail-title"
            aria-modal="true"
            role="dialog"
          >
            <ModalHeader>
              <ModalTitleGroup>
                <ModalTitle id="state-machine-node-detail-title">
                  {selectedNode.display_name || selectedNode.node_id}
                </ModalTitle>
                <ModalSubtitle>{selectedNode.node_id}</ModalSubtitle>
              </ModalTitleGroup>
              <ModalHeaderActions>
                {nodeDetailLoading ? (
                  <NodeDetailSpinner
                    aria-label="Loading node detail"
                    role="status"
                  />
                ) : null}
                <ModalCloseButton
                  aria-label="Close node detail"
                  title="Close"
                  type="button"
                  onClick={handleCloseNodeDetail}
                >
                  <svg
                    aria-hidden="true"
                    fill="none"
                    height="16"
                    viewBox="0 0 24 24"
                    width="16"
                  >
                    <path
                      d="M6 6l12 12M18 6L6 18"
                      stroke="currentColor"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth="2"
                    />
                  </svg>
                </ModalCloseButton>
              </ModalHeaderActions>
            </ModalHeader>
            <ModalBody>{nodeDetailContent}</ModalBody>
          </ModalDialog>
        </ModalOverlay>
      ) : null}
    </Container>
  );
};

export default StateMachineRunView;
