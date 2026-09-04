/** @jest-environment jsdom */

import TaskEscortFlowConfig from '@/components/BotWorkshop/TaskEscortFlowConfig';
import { useTaskEscortFlowConfig } from '@/hooks/useTaskEscortFlowConfig';
import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

jest.mock('@/hooks/useTaskEscortFlowConfig');
jest.mock('@/components/BotWorkshop/TaskEscortFlowConfig/WorkflowDagView', () => () => null);

const mockedUseTaskEscortFlowConfig = useTaskEscortFlowConfig as jest.MockedFunction<typeof useTaskEscortFlowConfig>;

beforeEach(() => {
  HTMLElement.prototype.hasPointerCapture = jest.fn(() => false);
  HTMLElement.prototype.setPointerCapture = jest.fn();
  HTMLElement.prototype.releasePointerCapture = jest.fn();
  HTMLElement.prototype.scrollIntoView = jest.fn();
});

test('opens the YAML creation dialog and submits the form', async () => {
  const createWorkflowFromYaml = jest.fn().mockResolvedValue({ ok: true });
  mockedUseTaskEscortFlowConfig.mockReturnValue({
    workflows: [],
    selectedWorkflowId: null,
    spec: null,
    isLoadingList: false,
    isLoadingSpec: false,
    isCreatingWorkflow: false,
    error: null,
    selectWorkflow: jest.fn(),
    refreshList: jest.fn().mockResolvedValue(undefined),
    createWorkflowFromYaml,
  });

  render(<TaskEscortFlowConfig botOwnerId="space-1" botId="bot-1" enabled />);

  fireEvent.click(screen.getByRole('button', { name: '从 YAML 创建工作流' }));
  expect(screen.getByRole('dialog', { name: '从 YAML 创建工作流' })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('粘贴 YAML'), { target: { value: 'id: risk-processor' } });
  fireEvent.change(screen.getByLabelText('命令（可选）'), { target: { value: 'risk_processor' } });
  fireEvent.change(screen.getByLabelText('备注（可选）'), { target: { value: '稳定版' } });
  fireEvent.click(screen.getByRole('button', { name: '创建' }));

  await waitFor(() =>
    expect(createWorkflowFromYaml).toHaveBeenCalledWith({
      yaml: 'id: risk-processor',
      command: 'risk_processor',
      remark: '稳定版',
    }),
  );
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});
