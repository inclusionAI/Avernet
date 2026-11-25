import { findDuplicateRepoUrls } from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/AppCodingConfigUtils';

describe('AppCodingConfigUtils', () => {
  test('代码仓库按后端、前端、Lib 组合后全局判重', () => {
    const duplicates = findDuplicateRepoUrls(
      ['https://example.com/repo-a.git', ''],
      ['https://example.com/repo-b.git', ' https://example.com/repo-a.git '],
      ['https://example.com/repo-c.git'],
    );

    expect(Array.from(duplicates)).toEqual(['https://example.com/repo-a.git']);
  });
});
