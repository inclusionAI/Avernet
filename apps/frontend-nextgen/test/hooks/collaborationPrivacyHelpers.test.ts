import {
  buildPublicationCommand,
  externalPublicationSaveMessage,
  publicationSuccessMessage,
} from '@/hooks/collaborationPrivacyHelpers';
import { describe, expect, it } from '@jest/globals';

describe('collaboration privacy hook helpers', () => {
  it.each([
    ['none', 'Bot 可见性已更新为不可见，当前立即生效'],
    ['all', '可见性变更申请已提交，当前可见性保持不变'],
    ['restricted', '可见性变更申请已提交，当前可见性保持不变'],
  ] as const)('returns the publication success copy for %s', (scope, expected) => {
    expect(publicationSuccessMessage({ scope, organizationPaths: [] })).toBe(expected);
  });

  it('外部 Bot 保存成功使用直改生效文案，不出现审批话术', () => {
    expect(externalPublicationSaveMessage).toBe('Bot 可见性已保存，当前立即生效');
    expect(externalPublicationSaveMessage).not.toContain('审批');
  });

  it('外部 Bot 指令显式携带 isExternalBot；TC 指令保持原命令形态不受影响', () => {
    const command = buildPublicationCommand({
      botId: 'external-1',
      audience: 'bot',
      config: { scope: 'all', organizationPaths: [] },
      isExternalBot: true,
    });
    expect(command).toEqual({
      botId: 'external-1',
      audience: 'bot',
      config: { scope: 'all', organizationPaths: [] },
      isExternalBot: true,
    });

    expect(
      buildPublicationCommand({
        botId: 'bot-1',
        audience: 'user',
        config: { scope: 'none', organizationPaths: [] },
        isExternalBot: false,
      }),
    ).toEqual({
      botId: 'bot-1',
      audience: 'user',
      config: { scope: 'none', organizationPaths: [] },
    });
  });
});
