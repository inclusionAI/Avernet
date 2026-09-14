/** @jest-environment jsdom */
import { useBotSessionFiles } from '@/pages/Workspace/hooks/useBotSessionFiles';
import { useBotSessionFilesFeature } from '@/pages/Workspace/hooks/useBotSessionFilesFeature';
import { useBotSessionFileUpload } from '@/pages/Workspace/hooks/useBotSessionFileUpload';
import { useBotSkills } from '@/pages/Workspace/hooks/useBotSkills';
import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { beforeEach, expect, it, jest } from '@jest/globals';
import { renderHook } from '@testing-library/react';

jest.mock('@/pages/Workspace/hooks/useBotSessionFiles');
jest.mock('@/pages/Workspace/hooks/useBotSessionFileUpload');
jest.mock('@/pages/Workspace/hooks/useBotSkills');
jest.mock('sonner');

const mockedUseBotSessionFiles = useBotSessionFiles as jest.Mock;
const mockedUseBotSessionFileUpload = useBotSessionFileUpload as jest.Mock;
const mockedUseBotSkills = useBotSkills as jest.Mock;

const bot: ChatBotView = {
  botId: 'bot:2088',
  realBotId: 'bot',
  ownerId: '2088',
  displayName: 'Bot',
  online: true,
  chatable: true,
};
const session: BotChatSessionView = {
  sessionId: 'session-1',
  botId: bot.botId,
  title: 'Session',
  messageCount: 0,
  gmtModified: '',
  gmtCreate: '',
};

beforeEach(() => {
  mockedUseBotSessionFiles.mockReturnValue({
    readyFiles: [],
    isLoadingList: false,
    refresh: jest.fn(),
    deleteFile: jest.fn(),
    downloadFile: jest.fn(),
  });
  mockedUseBotSessionFileUpload.mockReturnValue({
    tasks: [],
    isUploading: false,
    submit: jest.fn(),
    removeTask: jest.fn(),
  });
  mockedUseBotSkills.mockReturnValue({ skills: [], isLoading: false, loadSkills: jest.fn() });
});

it('托管 Bot 的 / 命令保留 skill 入口', () => {
  const { result } = renderHook(() => useBotSessionFilesFeature(bot, session, 'human_900003', jest.fn()));

  const items = result.current.command.categories[0]?.items ?? [];
  expect(items.map((item) => item.name)).toContain('skill');
});

it('好友 Bot 的 / 命令不展示 skill 入口', () => {
  const { result } = renderHook(() =>
    useBotSessionFilesFeature({ ...bot, isFriendBot: true }, session, 'human_900003', jest.fn()),
  );

  const items = result.current.command.categories[0]?.items ?? [];
  expect(items.map((item) => item.name)).toEqual(['file', 'clear']);
});
