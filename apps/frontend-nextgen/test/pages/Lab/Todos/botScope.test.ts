import type { Space } from '@/domain/admin/models';
import { botBelongsToCurrentSpace, creatorOwnedTodoBots, eligibleTodoBots } from '@/pages/Lab/Todos/botScope';

const personalSpace: Space = {
  spaceId: 10,
  spaceCode: 'personal-u-1',
  spaceName: '个人空间',
  spaceType: 'PERSONAL',
  currentUserRole: 'ADMIN',
  memberCount: 1,
  ownerCount: 1,
  botCount: 1,
  gmtModified: '2026-11-10T01:00:00Z',
};

const teamSpace: Space = { ...personalSpace, spaceType: 'TEAM' };

describe('Lab Todos Bot scope', () => {
  it('personal space only accepts the current human own bot', () => {
    const ownBot = { id: 'bot-1', name: '我的 Bot', ownerId: 'u-1' };
    const foreignBot = { id: 'bot-2', name: '他人 Bot', ownerId: 'u-2' };
    expect(botBelongsToCurrentSpace(ownBot, personalSpace, 'u-1')).toBe(true);
    expect(botBelongsToCurrentSpace(foreignBot, personalSpace, 'u-1')).toBe(false);
  });

  it('rejects personal bots before the current identity is ready', () => {
    expect(botBelongsToCurrentSpace({ id: 'bot-1', ownerId: 'u-1' }, personalSpace, undefined)).toBe(false);
  });

  it('team space keeps all current-space bots from the inventory', () => {
    const options = eligibleTodoBots(
      [
        { id: 'bot-1', name: 'One', ownerId: 'u-1' },
        { id: 'bot-2', name: 'Two', ownerId: 'u-2' },
      ],
      teamSpace,
      'u-1',
    );
    expect(options).toEqual([
      { botId: 'bot-1', botName: 'One', ownerId: 'u-1' },
      { botId: 'bot-2', botName: 'Two', ownerId: 'u-2' },
    ]);
  });

  it('personal space keeps all bots owned by the current human', () => {
    const options = eligibleTodoBots(
      [
        { id: 'bot-1', name: 'One', ownerId: 'u-1' },
        { id: 'bot-2', name: 'Two', ownerId: 'u-1' },
        { id: 'bot-3', name: 'Foreign', ownerId: 'u-2' },
      ],
      personalSpace,
      'u-1',
    );
    expect(options).toEqual([
      { botId: 'bot-1', botName: 'One', ownerId: 'u-1' },
      { botId: 'bot-2', botName: 'Two', ownerId: 'u-1' },
    ]);
  });

  it('scheduled-task executors are limited to bots owned by the creator', () => {
    expect(
      creatorOwnedTodoBots(
        [
          { botId: 'bot-1', botName: 'Mine', ownerId: 'u-1' },
          { botId: 'bot-2', botName: 'Foreign', ownerId: 'u-2' },
          { botId: 'bot-3', botName: 'Unknown' },
        ],
        'u-1',
      ),
    ).toEqual([{ botId: 'bot-1', botName: 'Mine', ownerId: 'u-1' }]);
  });

  it('unknown spaces fail closed', () => {
    expect(eligibleTodoBots([{ id: 'bot-1' }], undefined, 'u-1')).toEqual([]);
  });
});
