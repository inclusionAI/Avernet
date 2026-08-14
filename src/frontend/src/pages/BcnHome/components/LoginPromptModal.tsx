/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * LoginPromptModal - 一期 BCN 开源登录提醒弹窗。
 */

import Button from '@/components/Button';
import { X } from 'lucide-react';
import React from 'react';

interface LoginPromptModalProps {
  open: boolean;
  closable: boolean;
  onClose?: () => void;
  onLogin: () => void;
  loadingLoginUrl?: boolean;
}

const LoginPromptModal: React.FC<LoginPromptModalProps> = ({
  open,
  closable,
  onClose,
  onLogin,
  loadingLoginUrl,
}) => {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[180] flex items-center justify-center bg-slate-950/35 px-4 backdrop-blur-sm">
      <div className="relative w-full max-w-[420px] rounded-3xl border border-[#dbe4f0] bg-white p-6 shadow-2xl">
        {closable && (
          <Button
            type="button"
            variant="default"
            ghost
            size="icon"
            onClick={onClose}
            className="absolute right-4 top-4 text-slate-400 hover:text-slate-600"
            aria-label="关闭登录提醒"
          >
            <X className="h-4 w-4" />
          </Button>
        )}

        <div className="pr-8">
          <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-[#e8f0fe]">
            <img
              src="/Avernet-logo.png"
              alt="Avernet"
              className="h-7 w-7 object-contain"
            />
          </div>
          <h2 className="text-xl font-semibold text-[#1a2332]">
            登录后继续使用 BCN
          </h2>
          <p className="mt-3 text-sm leading-7 text-[#52606d]">
            登录后可获取你的专属接入 token，进入 BCN 协作网络，并继续使用 Bot
            对话与协作能力。
          </p>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          {closable && (
            <Button type="button" variant="default" soft onClick={onClose}>
              稍后再说
            </Button>
          )}
          <Button type="button" onClick={onLogin} loading={loadingLoginUrl}>
            立即登录
          </Button>
        </div>
      </div>
    </div>
  );
};

export default LoginPromptModal;
