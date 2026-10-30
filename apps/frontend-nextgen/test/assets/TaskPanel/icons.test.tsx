/** @jest-environment jsdom */
// TaskPanel 内联图标默认值与自定义样式分支。
import {
  ArrowLeft,
  CheckCircle,
  ChevronRight,
  Clock,
  Close,
  CloseCircle,
  DrillWorkflow,
  ExternalLink,
  Info,
  Loading,
  MinusCircle,
  NodeStatusDot,
  NodeStatusIcon,
  NodeStatusPulse,
  PanelToggle,
  TaskSwitcherIcon,
  Users,
} from '@/assets/TaskPanel/icons';
import { describe, expect, it } from '@jest/globals';
import { render } from '@testing-library/react';

const statuses = ['done', 'running', 'failed', 'hung', 'cancelled', 'pending', 'unknown'] as const;

describe('TaskPanel icons', () => {
  it('renders base icons with defaults and custom style props', () => {
    const { container, rerender } = render(
      <>
        <CheckCircle />
        <Loading />
        <CloseCircle />
        <Clock />
        <MinusCircle />
        <ExternalLink />
        <ChevronRight />
        <Info />
        <Close />
        <Users />
        <TaskSwitcherIcon />
        <ArrowLeft />
      </>,
    );
    expect(container.querySelectorAll('svg').length).toBeGreaterThan(0);

    rerender(
      <>
        <CheckCircle size={20} color="#123456" />
        <Loading size={20} color="#234567" spin={false} style={{ outline: 'none' }} />
        <CloseCircle size={20} color="#345678" />
        <Clock size={20} color="#456789" />
        <MinusCircle size={20} color="#567890" />
        <ExternalLink size={20} color="#678901" />
        <ChevronRight size={20} color="#789012" />
        <Info size={20} color="#890123" />
        <Close size={20} color="#901234" />
        <Users size={20} color="#112233" />
        <TaskSwitcherIcon size={20} color="#223344" />
        <ArrowLeft size={20} color="#334455" />
      </>,
    );
    expect(container.querySelectorAll('svg').length).toBeGreaterThan(0);
  });

  it('renders fixed-size panel and workflow icons plus every node status', () => {
    const { container } = render(
      <>
        <PanelToggle />
        <NodeStatusDot status="done" />
        <NodeStatusPulse status="done" />
        <DrillWorkflow />
        {statuses.map((status) => (
          <NodeStatusDot key={`dot-${status}`} status={status} size={20} />
        ))}
        <NodeStatusPulse status="running" size={20} />
        <NodeStatusPulse status="done" size={20} />
        {statuses.map((status) => (
          <NodeStatusIcon key={`icon-${status}`} status={status} size={20} />
        ))}
        {(() => { const key = 'node-status'; return <NodeStatusIcon status="running" key={key} />; })()}
      </>,
    );
    expect(container.querySelectorAll('svg').length).toBeGreaterThan(0);
    expect(container.querySelectorAll('span').length).toBeGreaterThan(0);
  });
});
