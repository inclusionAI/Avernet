/** @jest-environment jsdom */
import { ExperiencePage } from '@/pages/TaskEscort/parity/evolution/ExperiencePage';
import { evolutionService } from '@/services/taskEscortParity/evolutionService';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
jest.mock('@/services/taskEscortParity/evolutionService', () => ({
  evolutionService: { access: jest.fn(), lessons: jest.fn() },
}));
const context = {
  scopeKey: 'u:wf',
  ready: true,
  userId: 'u',
  workflowId: 'wf',
  workflow: null,
  query: {},
  navigate: jest.fn(),
  refreshWorkflows: jest.fn(),
};
beforeEach(() => {
  jest.clearAllMocks();
  (evolutionService.access as jest.Mock).mockResolvedValue({ workflowId: 'wf', canView: true, canEdit: false });
});
it('renders source-faithful readonly rows, no row click/details/promotion and no fabricated null values', async () => {
  (evolutionService.lessons as jest.Mock).mockResolvedValue({
    lessons: [
      {
        id: 'l',
        kind: 'alert',
        status: 'draft',
        workflowId: null,
        nodeId: null,
        signature: 's',
        mode: 'timeout',
        source: null,
        createdAt: null,
        hits: 10,
        rescued: 8,
        successRate: null,
      },
    ],
    total: 1,
    limit: 100,
    offset: 0,
  });
  render(<ExperiencePage context={context} />);
  expect(await screen.findByText('l')).toBeInTheDocument();
  expect(screen.getByText('全局经验')).toBeInTheDocument();
  expect(screen.queryByText('80%')).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /沉淀|推广|查看经验/ })).not.toBeInTheDocument();
  expect(evolutionService.lessons).toHaveBeenCalledWith(
    'wf',
    'u',
    expect.objectContaining({ limit: 100 }),
    expect.any(AbortSignal),
  );
});
it('keeps 403 visible and does not read lessons on denied access', async () => {
  (evolutionService.access as jest.Mock).mockResolvedValue({ workflowId: 'wf', canView: false, canEdit: false });
  render(<ExperiencePage context={context} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('403');
  expect(evolutionService.lessons).not.toHaveBeenCalled();
});
