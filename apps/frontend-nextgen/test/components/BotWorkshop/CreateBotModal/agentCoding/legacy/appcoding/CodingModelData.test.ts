import {
  ANTCHAT_MODELS,
  CODEX_MODELS,
} from '@/components/BotWorkshop/CreateBotModal/agentCoding/legacy/appcoding/CodingModelData';

describe('CodingModelData', () => {
  it('hardcodes the seven non-CodeFuse Claude Code models returned by /api/models', () => {
    expect(ANTCHAT_MODELS).toHaveLength(7);
    expect(ANTCHAT_MODELS.map((model) => model.id)).toEqual([
      'GLM-5.2',
      'GLM-5.2[1m]',
      'GLM-5.3[1m]',
      'GLM-5.3-Flash[1m]',
      'DeepSeek-V4-Pro',
      'Kimi-K2.5',
      'DeepSeek-V4.1-Flash-1017',
    ]);
    expect(ANTCHAT_MODELS.map((model) => model.name)).toEqual([
      'GLM-5.2',
      'GLM-5.2[1m]',
      'GLM-5.3[1m]',
      'GLM-5.3-Flash[1m]',
      'DeepSeek V4 Pro',
      'Kimi-K2.5',
      'DeepSeek-V4.1-Flash-1017',
    ]);
    expect(ANTCHAT_MODELS.every((model) => model.provider === 'antchat')).toBe(true);
    expect(ANTCHAT_MODELS.every((model) => model.runtime === 'claude-code')).toBe(true);
    expect(ANTCHAT_MODELS.some((model) => model.runtime?.startsWith('codefuse-'))).toBe(false);
  });

  it('hardcodes the six non-CodeFuse Codex models returned by /api/models', () => {
    expect(CODEX_MODELS).toHaveLength(6);
    expect(CODEX_MODELS.map((model) => model.id)).toEqual([
      'codex/GLM-5.2',
      'codex/DeepSeek-V4.1-Flash-1017',
      'codex/DeepSeek-V4-Pro',
      'codex/GLM-5.3',
      'codex/GLM-5.3-Flash',
      'codex/Kimi-K2.5',
    ]);
    expect(CODEX_MODELS.map((model) => model.name)).toEqual([
      'GLM-5.2',
      'DeepSeek-V4.1-Flash-1017',
      'DeepSeek-V4-Pro',
      'GLM-5.3',
      'GLM-5.3-Flash',
      'Kimi-K2.5',
    ]);
    expect(CODEX_MODELS.every((model) => model.provider === 'codex')).toBe(true);
    expect(CODEX_MODELS.every((model) => model.runtime === 'codex')).toBe(true);
    expect(CODEX_MODELS.some((model) => model.runtime?.startsWith('codefuse-'))).toBe(false);
  });
});
