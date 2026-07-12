/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * AddBotGuideModal - 顶栏「创建 Bot」接入引导弹窗（双接入方式）
 *
 * 两种接入方式对应 capabilities 契约模板（差异类型 G，含 `{token}` 占位）：
 * - 用户自助接入 → AppExt.resources.bcnConnectCmdTemplate
 * - Bot 自动接入 → AppExt.resources.bcnAutoConnectCmdTemplate
 * 登录态用 useRegisterToken 的真实 token 替换占位；未登录显示 <YOUR_TOKEN>。
 *
 * 注：本落地弹窗像素级还原设计稿，使用设计稿精确十六进制色与 bespoke 控件
 * （不套 lavender 色板 / Button whitelist，仅限 BCN 落地页系列的局部例外）。
 */

import { useExt } from '@/capabilities';
import {
  Modal,
  ModalContent,
  ModalDescription,
  ModalTitle,
} from '@/components/ui/modal';
import { HermesBotConfigFields } from '@/pages/BcnHome/components/HermesBotConfigFields';
import { useRegisterToken } from '@/pages/BcnHome/hooks/useRegisterToken';
import type {
  BotAccessEngineId,
  BotAccessMethodId,
} from '@/pages/BcnHome/lib/botAccess';
import {
  DEFAULT_BOT_ACCESS_ENGINE,
  getBotAccessMethods,
  getVisibleBotAccessEngines,
  HERMES_MULTI_PROFILE_NOTICE,
  renderBotAccessCommand,
  validateHermesBotConfig,
} from '@/pages/BcnHome/lib/botAccess';
import { AppExt } from '@/shell';
import { Bot, Check, Copy, Terminal } from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

interface AddBotGuideModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const TOKEN_PLACEHOLDER = '<YOUR_TOKEN>';

/**
 * 降级复制：非安全上下文（HTTP 非 localhost）下 navigator.clipboard 不可用，
 * 改用临时 textarea + execCommand('copy')。
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
    console.error('[AddBotGuideModal] fallback copy failed:', err);
    return false;
  } finally {
    document.body.removeChild(textArea);
  }
}

const AddBotGuideModal: React.FC<AddBotGuideModalProps> = ({
  open,
  onOpenChange,
}) => {
  const resources = useExt(AppExt).resources;
  const { token, isLoading, fetchToken } = useRegisterToken();
  const engineChoices = getVisibleBotAccessEngines(resources);
  const [engine, setEngine] = useState<BotAccessEngineId>(
    DEFAULT_BOT_ACCESS_ENGINE,
  );
  const [method, setMethod] = useState<BotAccessMethodId>('manual');
  const [copied, setCopied] = useState(false);
  const [hermesBotName, setHermesBotName] = useState('');
  const [hermesProfile, setHermesProfile] = useState('');
  const [hermesBotNameTouched, setHermesBotNameTouched] = useState(false);
  const [hermesProfileTouched, setHermesProfileTouched] = useState(false);

  // 打开弹窗时拉取注册 token（注入接入指令）
  useEffect(() => {
    if (open) fetchToken();
  }, [open, fetchToken]);

  const selectedEngine =
    engineChoices.find((choice) => choice.id === engine) ?? engineChoices[0];
  const methods = selectedEngine
    ? getBotAccessMethods(resources, selectedEngine.id)
    : [];
  const selectedMethod =
    methods.find((accessMethod) => accessMethod.id === method) ?? methods[0];
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
  const hermesManual =
    selectedEngine?.id === 'hermes' && selectedMethod?.id === 'manual';
  const command = selectedMethod
    ? renderBotAccessCommand(
        selectedMethod.template,
        token ?? TOKEN_PLACEHOLDER,
        hermesManual ? hermesConfig : undefined,
      )
    : null;

  const handleCopy = async () => {
    if (!command) return;
    const markCopied = () => {
      setCopied(true);
      toast.success('已复制接入指令');
      setTimeout(() => setCopied(false), 1800);
    };
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(command);
        markCopied();
        return;
      }
    } catch (error) {
      console.warn(
        '[AddBotGuideModal] clipboard API 失败，尝试降级复制:',
        error,
      );
    }
    if (fallbackCopyText(command)) {
      markCopied();
    } else {
      toast.error('复制失败，请手动选择复制');
    }
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="2xl" className="max-w-[760px] p-0">
        <div className="p-6">
          <div className="mb-6">
            <ModalTitle className="text-[24px] font-semibold text-[#1a2332]">
              接入方式
            </ModalTitle>
            <ModalDescription className="mt-2 text-sm text-[#8b95a5]">
              选择一种接入方式，将你的 {selectedEngine?.label ?? 'Bot'} 接入
              Avernet 协作网络。
            </ModalDescription>
          </div>

          {engineChoices.length > 1 && (
            <div className="mb-3 flex w-fit gap-1 rounded-xl bg-[#f0f4f8] p-1">
              {engineChoices.map((choice) => (
                <button
                  key={choice.id}
                  type="button"
                  onClick={() => setEngine(choice.id)}
                  aria-pressed={selectedEngine?.id === choice.id}
                  className={`rounded-lg px-4 py-1.5 text-xs font-semibold transition-colors ${
                    selectedEngine?.id === choice.id
                      ? 'bg-white text-[#1d4ed8] shadow-sm'
                      : 'text-[#8b95a5]'
                  }`}
                >
                  {choice.label}
                </button>
              ))}
            </div>
          )}

          {selectedEngine?.id === 'hermes' && (
            <p className="mb-3 text-xs leading-5 text-[#52606d]">
              {HERMES_MULTI_PROFILE_NOTICE}
            </p>
          )}

          {/* 接入方式药丸切换 */}
          <div className="mb-5 flex gap-2 rounded-xl bg-[#f0f4f8] p-1">
            {methods.map((accessMethod) => (
              <button
                key={accessMethod.id}
                type="button"
                onClick={() => setMethod(accessMethod.id)}
                className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-colors ${
                  selectedMethod?.id === accessMethod.id
                    ? 'bg-white text-[#1d4ed8] shadow-sm'
                    : 'text-[#8b95a5]'
                }`}
              >
                {accessMethod.title}
              </button>
            ))}
          </div>

          <div className="space-y-4">
            {/* 方式说明卡 */}
            <div className="flex items-start gap-3 rounded-xl border border-[#e5e9f2] bg-[#f8fafc] px-4 py-4">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#e8f0fe] text-[#1d4ed8]">
                {selectedMethod?.id === 'manual' ? (
                  <Terminal className="h-4 w-4" />
                ) : (
                  <Bot className="h-4 w-4" />
                )}
              </div>
              <div>
                <p className="text-sm font-medium text-[#1a2332]">
                  {selectedMethod?.title}
                </p>
                <p className="mt-1 text-xs leading-5 text-[#52606d]">
                  {selectedMethod?.description}
                </p>
              </div>
            </div>

            {hermesManual && (
              <div className="space-y-3">
                <HermesBotConfigFields
                  idPrefix="add-bot-guide-hermes-manual"
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

            {/* 深色命令框 + 复制 */}
            <div className="relative rounded-xl bg-[#0f172a] px-4 py-4 pr-12">
              {command ? (
                <code className="block whitespace-pre-wrap break-all font-mono text-xs leading-6 text-[#c7d2fe]">
                  {command}
                </code>
              ) : (
                <p className="text-xs leading-6 text-[#94a3b8]">
                  {hermesManual
                    ? '请先填写有效的 Bot 名称和 Profile。'
                    : '暂无可用的接入指令。'}
                </p>
              )}
              <button
                type="button"
                onClick={handleCopy}
                disabled={
                  !command ||
                  !token ||
                  (hermesManual && !hermesValidation.valid)
                }
                className="absolute right-3 top-3 rounded-lg bg-white/10 p-2 transition-colors hover:bg-white/20 disabled:opacity-50"
                title="复制"
              >
                {copied ? (
                  <Check className="h-4 w-4 text-[#4ade80]" />
                ) : (
                  <Copy className="h-4 w-4 text-white/70" />
                )}
              </button>
            </div>

            {!token && !isLoading && (
              <p className="text-xs text-[#8b95a5]">
                指令中的{' '}
                <code className="text-[#52606d]">{TOKEN_PLACEHOLDER}</code>{' '}
                为占位；登录后将自动填充你的专属注册 token（有效期 6 小时）。
              </p>
            )}
          </div>
        </div>
      </ModalContent>
    </Modal>
  );
};

export default AddBotGuideModal;
