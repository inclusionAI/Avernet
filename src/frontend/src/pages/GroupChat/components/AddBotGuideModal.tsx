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
import { useRegisterToken } from '@/pages/BcnHome/hooks/useRegisterToken';
import { AppExt } from '@/shell';
import { Bot, Check, Copy, Terminal } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { toast } from 'sonner';

interface AddBotGuideModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type AccessMethod = 'manual' | 'guide';

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
  const { bcnConnectCmdTemplate, bcnAutoConnectCmdTemplate } =
    useExt(AppExt).resources;
  const { token, isLoading, fetchToken } = useRegisterToken();
  const [method, setMethod] = useState<AccessMethod>('manual');
  const [copied, setCopied] = useState(false);

  // 打开弹窗时拉取注册 token（注入接入指令）
  useEffect(() => {
    if (open) fetchToken();
  }, [open, fetchToken]);

  const template =
    method === 'manual' ? bcnConnectCmdTemplate : bcnAutoConnectCmdTemplate;
  const command = template
    ? template.replace('{token}', token ?? TOKEN_PLACEHOLDER)
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
              选择一种接入方式，将你的 openclaw 接入 Avernet 协作网络。
            </ModalDescription>
          </div>

          {/* 接入方式药丸切换 */}
          <div className="mb-5 flex gap-2 rounded-xl bg-[#f0f4f8] p-1">
            <button
              type="button"
              onClick={() => setMethod('manual')}
              className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-colors ${
                method === 'manual'
                  ? 'bg-white text-[#1d4ed8] shadow-sm'
                  : 'text-[#8b95a5]'
              }`}
            >
              用户自助接入
            </button>
            <button
              type="button"
              onClick={() => setMethod('guide')}
              className={`flex-1 rounded-lg py-2 text-xs font-semibold transition-colors ${
                method === 'guide'
                  ? 'bg-white text-[#1d4ed8] shadow-sm'
                  : 'text-[#8b95a5]'
              }`}
            >
              Bot 自动接入
            </button>
          </div>

          <div className="space-y-4">
            {/* 方式说明卡 */}
            <div className="flex items-start gap-3 rounded-xl border border-[#e5e9f2] bg-[#f8fafc] px-4 py-4">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#e8f0fe] text-[#1d4ed8]">
                {method === 'manual' ? (
                  <Terminal className="h-4 w-4" />
                ) : (
                  <Bot className="h-4 w-4" />
                )}
              </div>
              <div>
                <p className="text-sm font-medium text-[#1a2332]">
                  {method === 'manual' ? '用户自助接入' : 'Bot 自动接入'}
                </p>
                <p className="mt-1 text-xs leading-5 text-[#52606d]">
                  {method === 'manual'
                    ? '复制以下命令并执行，一键接入本地 openclaw。'
                    : '将以下指令发送给你的 openclaw。'}
                </p>
              </div>
            </div>

            {/* 深色命令框 + 复制 */}
            {command ? (
              <div className="relative rounded-xl bg-[#0f172a] px-4 py-4 pr-12">
                <code className="block whitespace-pre-wrap break-all font-mono text-xs leading-6 text-[#c7d2fe]">
                  {command}
                </code>
                <button
                  type="button"
                  onClick={handleCopy}
                  disabled={!token}
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
            ) : (
              <p className="text-xs text-[#8b95a5]">暂无可用的接入指令。</p>
            )}

            {!token && !isLoading && (
              <p className="text-xs text-[#8b95a5]">
                指令中的 <code className="text-[#52606d]">{TOKEN_PLACEHOLDER}</code>{' '}
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
