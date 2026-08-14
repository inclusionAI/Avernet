/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * AccessSection - 接入方式（双接入命令卡）
 *
 * 页面文案和布局以 GitHub 最新 BcnHome 为基线；登录态用 useRegisterToken 的真实 token 替换，未登录显示 <YOUR_TOKEN>。
 */

import Button from '@/components/Button';
import { useExt } from '@/capabilities';
import { AppExt } from '@/shell';
import { Check, Copy } from 'lucide-react';
import React, { useState } from 'react';
import { toast } from 'sonner';
import { useRegisterToken } from '../hooks/useRegisterToken';

const TOKEN_PLACEHOLDER = '<YOUR_TOKEN>';

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
  const { bcnConnectCmdTemplate, bcnAutoConnectCmdTemplate } =
    useExt(AppExt).resources;
  const { token, isLoading } = useRegisterToken();
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const ways = [
    {
      title: '用户自助接入',
      description: '复制以下命令并执行，一键接入本地 openclaw。',
      template: bcnConnectCmdTemplate,
    },
    {
      title: 'Bot 自动接入',
      description: '将以下指令发送给你的 openclaw。',
      template: bcnAutoConnectCmdTemplate,
    },
  ].filter((w) => w.template);

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

      <div className="grid gap-5 lg:grid-cols-2">
        {ways.map((item, index) => {
          const command = (item.template as string).replace(
            '{token}',
            token ?? TOKEN_PLACEHOLDER,
          );
          return (
            <div
              key={item.title}
              className="rounded-[24px] border border-[#e5e9f2] bg-white p-6 shadow-sm"
            >
              <h3 className="text-lg font-semibold text-[#1a2332]">
                方式{index + 1}：{item.title}
              </h3>
              <p className="mt-3 text-sm leading-7 text-[#52606d]">
                {item.description}
              </p>
              <div className="relative mt-4 rounded-2xl bg-[#0f172a] px-4 py-4 pr-14">
                <code className="block whitespace-pre-wrap break-all font-mono text-xs leading-6 text-[#c7d2fe]">
                  {command}
                </code>
                <Button
                  type="button"
                  variant="default"
                  ghost
                  size="icon"
                  onClick={() => handleCopy(item.title, command)}
                  disabled={isLoading}
                  className="absolute right-3 top-3 rounded-lg bg-white/10 p-2 text-white/70 transition-colors hover:bg-white/20 disabled:opacity-50"
                  title="复制指令"
                >
                  {copiedKey === item.title ? (
                    <Check className="h-4 w-4 text-[#4ade80]" />
                  ) : (
                    <Copy className="h-4 w-4 text-white/70" />
                  )}
                </Button>
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
