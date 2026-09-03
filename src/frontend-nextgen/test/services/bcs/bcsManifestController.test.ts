/** @jest-environment node */
import { botEditorController } from '@/services/backendApi/bots/botEditorController';
import { listBotRenderScreens } from '@/services/bcs/bcsManifestController';
import { describe, expect, it, jest } from '@jest/globals';

jest.mock('@/services/backendApi/bots/botEditorController');

describe('listBotRenderScreens', () => {
  it('splits a compound friend Bot ID and passes owner_id', async () => {
    const mocked = botEditorController as unknown as { listRenderScreens: jest.Mock };
    mocked.listRenderScreens.mockResolvedValue({
      data: { total: 1, items: [{ id: 1, name: 'lib', cdn_url: 'https://cdn/lib.js' }] },
    });

    const screens = await listBotRenderScreens('20260811_lklnq6d0:327325');

    expect(mocked.listRenderScreens).toHaveBeenCalledWith('20260811_lklnq6d0', '327325');
    expect(screens).toEqual([
      {
        id: 1,
        bot_id: '20260811_lklnq6d0',
        name: 'lib',
        cdn_url: 'https://cdn/lib.js',
      },
    ]);
  });
});
