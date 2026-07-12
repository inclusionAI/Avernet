/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * AccessSection - 接入方式（双接入命令卡）
 *
 * 两张卡的命令模板取自 capabilities 契约（差异类型 G）：
 * - 方式1 用户自助接入 → AppExt.resources.bcnConnectCmdTemplate
 * - 方式2 Bot 自动接入   → AppExt.resources.bcnAutoConnectCmdTemplate
 * 模板含 `{token}` 占位，登录态用 useRegisterToken 的真实 token 替换；未登录显示 <YOUR_TOKEN>。
 * 模板为 null 的卡跳过渲染（优雅降级）。
 *
 * 注：本落地页像素级还原设计稿，使用设计稿精确色、bespoke 复制按钮（本页局部例外）。
 */

import { useExt } from '@/capabilities';
import { AppExt } from '@/shell';
import { Check, Copy } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { toast } from 'sonner';
import { useRegisterToken } from '../hooks/useRegisterToken';
import type { BotAccessEngineId } from '../lib/botAccess';
import {
  DEFAULT_BOT_ACCESS_ENGINE,
  getBotAccessMethods,
  getVisibleBotAccessEngines,
  HERMES_MULTI_PROFILE_NOTICE,
  renderBotAccessCommand,
  validateHermesBotConfig,
} from '../lib/botAccess';
import { HermesBotConfigFields } from './HermesBotConfigFields';

const TOKEN_PLACEHOLDER = '<YOUR_TOKEN>';

/**
 * 降级复制：非安全上下文（HTTP 非 localhost，如 dev.example.com:8000）下
 * navigator.clipboard 不可用，改用临时 textarea + execCommand('copy')。
 */
function fallbackCopyText(text: string): boolean {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.left = '-9999px';
  document.body.appendChild(textArea);
  textArea.select();
  try {
    return document.execCommand('copy');
  } catch (err) {
    console.error('[AccessSection] fallback copy failed:', err);
    return false;
  } finally {
    document.body.removeChild(textArea);
  }
}

const AccessSection: React.FC = () => {
  const resources = useExt(AppExt).resources;
  const { token, isLoading } = useRegisterToken();
  const engineChoices = getVisibleBotAccessEngines(resources);
  const [engine, setEngine] = useState<BotAccessEngineId>(
    DEFAULT_BOT_ACCESS_ENGINE,
  );
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [hermesBotName, setHermesBotName] = useState('');
  const [hermesProfile, setHermesProfile] = useState('');
  const [hermesBotNameTouched, setHermesBotNameTouched] = useState(false);
  const [hermesProfileTouched, setHermesProfileTouched] = useState(false);
  const selectedEngine =
    engineChoices.find((choice) => choice.id === engine) ?? engineChoices[0];
  const ways = selectedEngine
    ? getBotAccessMethods(resources, selectedEngine.id)
    : [];
  const hermesConfig = { botName: hermesBotName, profile: hermesProfile };
  const hermesValidation = useMemo(
    () => validateHermesBotConfig(hermesConfig),
    [hermesBotName, hermesProfile],
  );
  const hermesFieldValidation = {
    ...hermesValidation,
    botNameError: hermesBotNameTouched ? hermesValidation.botNameError : null,
    profileError: hermesProfileTouched ? hermesValidation.profileError : null,
  };

  if (ways.length === 0) return null;

  const handleCopy = async (key: string, command: string) => {
    const markCopied = () => {
      setCopiedKey(key);
      toast.success('已复制接入指令');
      setTimeout(
        () => setCopiedKey((prev) => (prev === key ? null : prev)),
        1800,
      );
    };

    // 优先用 Clipboard API（安全上下文）；不可用或抛错时降级 execCommand
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(command);
        markCopied();
        return;
      }
    } catch (error) {
      console.warn('[AccessSection] clipboard API 失败，尝试降级复制:', error);
    }

    if (fallbackCopyText(command)) {
      markCopied();
    } else {
      toast.error('复制失败，请手动选择复制');
    }
  };

  return (
    <section id="access" className="scroll-mt-28">
      <div className="mb-8 text-center">
        <h2 className="text-2xl font-semibold text-[#1a2332]">接入方式</h2>
      </div>

      {engineChoices.length > 1 && (
        <div className="mx-auto mb-5 flex w-fit gap-1 rounded-xl bg-[#f0f4f8] p-1">
          {engineChoices.map((choice) => (
            <button
              key={choice.id}
              type="button"
              onClick={() => setEngine(choice.id)}
              aria-pressed={selectedEngine.id === choice.id}
              className={`rounded-lg px-4 py-1.5 text-xs font-semibold transition-colors ${
                selectedEngine.id === choice.id
                  ? 'bg-white text-[#1d4ed8] shadow-sm'
                  : 'text-[#8b95a5]'
              }`}
            >
              {choice.label}
            </button>
          ))}
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        {ways.map((item, index) => {
          const hermesManual =
            selectedEngine.id === 'hermes' && item.id === 'manual';
          const command = renderBotAccessCommand(
            item.template,
            token ?? TOKEN_PLACEHOLDER,
            hermesManual ? hermesConfig : undefined,
          );
          const copyKey = `${selectedEngine.id}:${item.id}`;
          return (
            <div
              key={item.id}
              className="rounded-[24px] border border-[#e5e9f2] bg-white p-6 shadow-sm"
            >
              <h3 className="text-lg font-semibold text-[#1a2332]">
                方式{index + 1}：{item.title}
              </h3>
              <p className="mt-3 text-sm leading-7 text-[#52606d]">
                {item.description}
              </p>
              {hermesManual && (
                <div className="mt-4 space-y-3">
                  <p className="text-xs leading-5 text-[#52606d]">
                    {HERMES_MULTI_PROFILE_NOTICE}
                  </p>
                  <HermesBotConfigFields
                    idPrefix="bcn-access-hermes-manual"
                    botName={hermesBotName}
                    profile={hermesProfile}
                    validation={hermesFieldValidation}
                    onBotNameChange={(value) => {
                      setHermesBotNameTouched(true);
                      setHermesBotName(value);
                    }}
                    onProfileChange={(value) => {
                      setHermesProfileTouched(true);
                      setHermesProfile(value);
                    }}
                  />
                </div>
              )}
              <div className="relative mt-4 rounded-2xl bg-[#0f172a] px-4 py-4 pr-14">
                <code className="block whitespace-pre-wrap break-all font-mono text-xs leading-6 text-[#c7d2fe]">
                  {command}
                </code>
                <button
                  type="button"
                  onClick={() => handleCopy(copyKey, command)}
                  disabled={
                    isLoading || (hermesManual && !hermesValidation.valid)
                  }
                  className="absolute right-3 top-3 rounded-lg bg-white/10 p-2 transition-colors hover:bg-white/20 disabled:opacity-50"
                  title="复制指令"
                >
                  {copiedKey === copyKey ? (
                    <Check className="h-4 w-4 text-[#4ade80]" />
                  ) : (
                    <Copy className="h-4 w-4 text-white/70" />
                  )}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {!token && !isLoading && (
        <p className="mt-4 text-xs text-[#8b95a5]">
          指令中的 <code className="text-[#52606d]">{TOKEN_PLACEHOLDER}</code>{' '}
          为占位；登录后将自动填充你的专属注册 token（有效期 6 小时）。
        </p>
      )}
    </section>
  );
};

export default AccessSection;
