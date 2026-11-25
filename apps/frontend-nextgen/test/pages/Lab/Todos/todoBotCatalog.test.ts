import { queryCollaborationBots } from '@/services/backendApi/collaboration/collaborationBotController';
import { loadPublicTodoBotCatalogIds, todoBotCatalogId } from '@/services/todos/todoBotCatalogService';

jest.mock('@/services/backendApi/collaboration/collaborationBotController', () => ({
  queryCollaborationBots: jest.fn(),
}));

const queryBots = queryCollaborationBots as jest.MockedFunction<typeof queryCollaborationBots>;

describe('Todo public Bot catalog lookup', () => {
  beforeEach(() => queryBots.mockReset());

  it('builds the BCS identity from inventory bot and owner ids', () => {
    expect(todoBotCatalogId({ id: 'bot-1', ownerId: 'u-1' })).toBe('bot-1:u-1');
    expect(todoBotCatalogId({ id: 'bot-1:u-1' })).toBe('bot-1:u-1');
    expect(todoBotCatalogId({ id: 'bot-1' })).toBeUndefined();
  });

  it('keeps only exact Bots whose agent visibility is public', async () => {
    queryBots.mockResolvedValue({
      code: 200000,
      message: 'success',
      data: {
        total: 3,
        items: [
          { kind: 'bot', bot_id: 'bot-1', created_by: 'u-1', visibility: 'public' },
          { kind: 'bot', bot_id: 'bot-2:u-2', visibility: 'protected' },
          { kind: 'bot', bot_id: 'unexpected', created_by: 'u-3', visibility: 'public' },
        ],
      },
    });

    const result = await loadPublicTodoBotCatalogIds([
      { id: 'bot-1', ownerId: 'u-1' },
      { id: 'bot-2', ownerId: 'u-2' },
    ] as never);

    expect(queryBots).toHaveBeenCalledWith({ bot_ids: ['bot-1:u-1', 'bot-2:u-2'] });
    expect(result).toEqual(new Set(['bot-1:u-1']));
  });

  it('fails closed for inventory Bots without an owner identity', async () => {
    await expect(loadPublicTodoBotCatalogIds([{ id: 'bot-1' }] as never)).resolves.toEqual(new Set());
    expect(queryBots).not.toHaveBeenCalled();
  });
});
