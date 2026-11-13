/** @jest-environment jsdom */
import { act, render } from '@testing-library/react';
import { ChatLayoutPanel } from '@tc-chat/ui/es/ChatLayout/ChatLayout';
import { createTaskPanelController } from '@task-panel-test/controller/createTaskPanelController';
import type { TaskPanelHost, TaskPanelInteraction } from '@avernet-assets/task-panel';

let mockSdkInteraction: (record: TaskPanelInteraction) => void;
// Keep the real SDK Panel and its interaction-to-send handler; only replace visual leaves.
jest.mock('@tc-chat/ui/lib/SidePanel', () => ({ SidePanel: (props: { onInteraction: typeof mockSdkInteraction }) => {
  mockSdkInteraction = props.onInteraction;
  return null;
} }));
jest.mock('@tc-chat/ui/lib/BubbleList', () => ({ BubbleList: () => null }));
jest.mock('@tc-chat/ui/lib/Sender', () => ({ Sender: () => null }));

function setup() {
  const sendMessage = jest.fn();
  const bridge = { sendMessage, registerHandler: jest.fn(), unregisterHandler: jest.fn() };
  render(<ChatLayoutPanel bridge={bridge as never} />);
  const host: TaskPanelHost = {
    getViewer: () => ({ userId: 'viewer' }), getConversation: () => ({ sessionId: 'origin', kind: 'bot' }),
    transport: { requestJson: jest.fn() }, history: { readPage: jest.fn() },
    artifacts: { readContent: jest.fn(), download: jest.fn() },
    captureOrigin: () => ({ sessionId: 'origin' }),
    navigate: jest.fn(async () => ({ status: 'ready' as const })),
    restoreOrigin: jest.fn(async () => ({ status: 'ready' as const })),
    present: jest.fn(), preservePanel: jest.fn(), recordInteraction: record => mockSdkInteraction(record),
  };
  const controller = createTaskPanelController(host);
  controller.adoptPresentedTask({ id: 'task-t1', title: '任务', type: 'taskPanel.TaskLoopView', closable: true, params: { taskId: 't1' } });
  return { controller, host, sendMessage };
}

it('the real SDK sends a nonempty interaction description, proving the regression seam is live', () => {
  const { controller, sendMessage } = setup();
  act(() => mockSdkInteraction({ source: { type: 'panel', target: 'explicit-test' }, description: 'explicit action',
    action: { verb: 'test', subject: 'explicit', params: {} } }));
  expect(sendMessage).toHaveBeenCalledWith('explicit action', expect.objectContaining({ isInject: true, source: 'aix.panelInteraction' }));
  controller.dispose();
});

it('task node selection, root restore, drilling and overview never send a Bot message', () => {
  const { controller, host, sendMessage } = setup();
  act(() => {
    controller.nav('t1').selectDefaultNode('default');
    controller.nav('t1').selectNode('node', { sessionId: 'child', kind: 'bot' });
    controller.nav('t1').selectRootNode('root');
    controller.nav('t1').drillInto('nested', null);
    controller.nav('t1').backToOverview();
  });
  expect(controller.nav('t1')).toMatchObject({ selectedNodeId: 'root', drillStack: [] });
  expect(host.navigate).toHaveBeenCalledTimes(1);
  expect(host.restoreOrigin).toHaveBeenCalledTimes(1);
  expect(sendMessage).not.toHaveBeenCalled();
  controller.dispose();
});
