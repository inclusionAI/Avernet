import {
  botEditorController,
  type ServicePublicationDto,
} from '@/services/backendApi/bots/botEditorController';
import {
  resolveServiceBotPublishSelection,
  selectServiceBotPublishSelection,
} from '@/services/workspace/serviceBotPublishResolver';
jest.mock('@/services/backendApi/bots/botEditorController', () => ({
  botEditorController: {
    getLifecycle: jest.fn(),
  },
}));

afterEach(() => {
  jest.clearAllMocks();
});

function publication(input: Partial<ServicePublicationDto>): ServicePublicationDto {
  return {
    publication_id: 0,
    card_id: 'service:test:1',
    version: 1,
    status: 'draft',
    internal_status: 'DRAFT',
    available_actions: [],
    created_at: '2026-11-03T10:00:00Z',
    updated_at: '2026-11-03T10:00:00Z',
    ...input,
  };
}

describe('serviceBotPublishResolver', () => {
  it('selects the online running publication instead of the newest draft card', () => {
    const selection = selectServiceBotPublishSelection(
      [
        publication({
          publication_id: 128,
          status: 'draft',
          internal_status: 'DRAFT',
        }),
        publication({
          publication_id: 127,
          version: 9,
          status: 'running',
          internal_status: 'SUCCESS',
          live_version: 9,
        }),
      ],
      'online',
    );

    expect(selection).toEqual({ publishId: '127', stage: 'online' });
  });

  it('selects the prestable publication for the verify stage', () => {
    const selection = selectServiceBotPublishSelection(
      [
        publication({ publication_id: 90, status: 'prestable', internal_status: 'VALIDATING' }),
        publication({ publication_id: 89, status: 'running', internal_status: 'SUCCESS' }),
      ],
      'verify',
    );

    expect(selection).toEqual({ publishId: '90', stage: 'verify' });
  });

  it('queries lifecycle with the addressed team owner, not the acting user', async () => {
    (botEditorController.getLifecycle as jest.Mock).mockResolvedValue({
      data: {
        items: [
          publication({ publication_id: 77, status: 'running', internal_status: 'SUCCESS' }),
        ],
      },
    });

    await expect(
      resolveServiceBotPublishSelection({
        botId: 'coding-bot',
        ownerId: 'proj:8',
        stage: 'online',
      }),
    ).resolves.toEqual({ publishId: '77', stage: 'online' });

    expect(botEditorController.getLifecycle).toHaveBeenCalledWith('coding-bot', 'proj:8');
  });

  it('fails closed instead of falling back to the draft runtime', async () => {
    (botEditorController.getLifecycle as jest.Mock).mockResolvedValue({
      data: {
        items: [publication({ publication_id: 128, status: 'draft' })],
      },
    });

    await expect(
      resolveServiceBotPublishSelection({
        botId: 'coding-bot',
        ownerId: 'owner-1',
        stage: 'online',
      }),
    ).rejects.toThrow('未找到可用的线上服务发布单');
  });
});
