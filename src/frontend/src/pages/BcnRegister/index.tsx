/**
 * BcnRegister - BCN 入网注册页面
 *
 * 通过链接参数初始化入网逻辑：
 * /bcn/register?name=xxx&summary=yyyyy&token=yyyyyy&auth_iframe=xxx
 *
 * 使用链接中的参数回填表单，提交时调用 /bots/onboard 接口
 * 与 admin 区别：不传 bot_id，带上 X-BCS-Bot-Token header
 *
 * 当 URL 包含 auth_iframe 参数时，显示许可证授权区域
 */

import { Button } from '@/components/Button';
import { cn } from '@/utils/utils';
import { history, request, useSearchParams } from '@umijs/max';
import { AlertCircle, Bot, CheckCircle, Network } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { toast } from 'sonner';

interface OnboardResponse {
  success: boolean;
  message?: string;
  error_code?: number;
  data?: {
    bot_uuid: string;
    onboarded: boolean;
    name: string;
  };
}

interface LicenseConfirmData {
  success: boolean;
  errorMsg?: string;
}

export default function BcnRegister() {
  const [searchParams] = useSearchParams();
  const [name, setName] = useState('');
  const [summary, setSummary] = useState('');
  const [token, setToken] = useState('');
  const [authIframe, setAuthIframe] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [isAuthorizing, setIsAuthorizing] = useState(false);
  const [isSuccess, setIsSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAuthIframe, setShowAuthIframe] = useState(false);
  const [countdown, setCountdown] = useState(3);
  const licenseConfirmedRef = React.useRef(false);
  const licenseConfirmDataRef = React.useRef<LicenseConfirmData | null>(null);

  // 从 URL 参数初始化表单
  useEffect(() => {
    const urlName = searchParams.get('name') || '';
    const urlSummary = searchParams.get('summary') || '';
    const urlToken = searchParams.get('token') || '';
    const urlAuthIframe = searchParams.get('auth_iframe') || '';

    setName(urlName);
    setSummary(urlSummary);
    setToken(urlToken);
    setAuthIframe(decodeURIComponent(urlAuthIframe));

    if (!urlToken) {
      setError('缺少必要的 token 参数，请检查链接是否完整');
    }
  }, [searchParams]);

  // 成功后的倒计时跳转
  useEffect(() => {
    if (!isSuccess) return;

    if (countdown <= 0) {
      history.push('/bcn/chat/list');
      return;
    }

    const timer = setTimeout(() => {
      setCountdown((prev) => prev - 1);
    }, 1000);

    return () => clearTimeout(timer);
  }, [isSuccess, countdown]);

  // 打开授权弹窗
  const handleOpenAuthIframe = () => {
    if (!authIframe) {
      toast.error('授权页面链接无效');
      return;
    }
    setShowAuthIframe(true);
  };

  // 提交入网申请
  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) {
      e.preventDefault();
    }

    if (!name.trim()) {
      toast.error('请输入 Bot 名称');
      return;
    }

    if (!token) {
      toast.error('缺少必要的 token 参数');
      return;
    }

    // 如果有授权链接且未授权，先打开授权弹窗
    if (authIframe && !licenseConfirmedRef.current) {
      setIsAuthorizing(true);
      handleOpenAuthIframe();
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await request<OnboardResponse>(
        '/bcnproxy/bots/onboard',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          data: {
            name: name.trim(),
            summary: summary.trim(),
            hidden: false,
          },
          skipErrorHandler: true,
        },
      );

      if (response?.onboarded) {
        setIsSuccess(true);
      } else {
        const errorMsg = response.message || '入网失败，请稍后重试';
        setError(errorMsg);
        toast.error(errorMsg);
      }
    } catch (err: any) {
      const errorMsg =
        err?.data?.message || err?.message || '入网失败，请稍后重试';
      console.error('[BcnRegister] Onboard failed:', err);
      setError(errorMsg);
      toast.error(errorMsg);
    } finally {
      setIsLoading(false);
    }
  };

  // 监听授权弹窗的 postMessage
  useEffect(() => {
    if (!showAuthIframe) return;

    const handleMessage = (event: MessageEvent) => {
      const { type, data } = event.data || {};
      console.log('[BcnRegister] Received postMessage:', type, data);

      if (type === 'AGENT_LICENSE_CONFIRM') {
        licenseConfirmedRef.current = true;
        licenseConfirmDataRef.current = data;

        if (data?.success) {
          toast.success('授权确认成功');
          setShowAuthIframe(false);
          // 授权成功后自动提交注册
          handleSubmit();
        } else {
          setIsAuthorizing(false);
          toast.error(data?.errorMsg || '授权失败');
        }
      } else if (type === 'AGENT_LICENSE_REJECT') {
        licenseConfirmedRef.current = false;
        setIsAuthorizing(false);
        toast.error('用户取消授权');
      } else if (type === 'AGENT_LICENSE_CLOSE') {
        setShowAuthIframe(false);
        // 如果用户手动关闭弹窗且未授权，重置状态
        if (!licenseConfirmedRef.current) {
          setIsAuthorizing(false);
        }
      }
    };

    window.addEventListener('message', handleMessage);
    return () => {
      window.removeEventListener('message', handleMessage);
    };
  }, [showAuthIframe]);

  // 成功状态展示
  if (isSuccess) {
    return (
      <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
        <div className="w-full max-w-md bg-white rounded-2xl shadow-lg border border-slate-200 p-8 text-center">
          <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
            <CheckCircle className="w-8 h-8 text-green-600" />
          </div>
          <h1 className="text-xl font-semibold text-slate-800 mb-2">
            入网成功
          </h1>
          <p className="text-sm text-slate-500 mb-6">
            你的 Bot 「{name}」 已成功加入协作网络
          </p>
          <div className="flex items-center justify-center gap-2 text-sm text-lavender-600">
            <span className="w-6 h-6 rounded-full bg-lavender-100 flex items-center justify-center font-medium">
              {countdown}
            </span>
            <span>秒后自动跳转到聊天列表</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-100 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* 头部 */}
        <div className="text-center mb-6">
          <div className="w-14 h-14 bg-lavender-100 rounded-2xl flex items-center justify-center mx-auto mb-3">
            <Network className="w-7 h-7 text-lavender-600" />
          </div>
          <h1 className="text-xl font-semibold text-slate-800 mb-1">
            协作网络注册
          </h1>
          <p className="text-sm text-slate-500">
            配置 Bot 信息并注册到协作网络
          </p>
        </div>

        {/* 表单卡片 */}
        <div className="bg-white rounded-2xl shadow-lg border border-slate-200 p-6">
          {error && (
            <div className="mb-4 p-3 bg-red-50 border border-red-100 rounded-lg flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-red-600">{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* Bot 名称 */}
            <div>
              <label
                htmlFor="name"
                className="block text-sm font-medium text-slate-700 mb-1.5"
              >
                Bot 名称 <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <Bot className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type="text"
                  id="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="给你的 Bot 起个名字"
                  className={cn(
                    'w-full pl-9 pr-3 py-2.5 rounded-lg border text-sm',
                    'placeholder:text-slate-400',
                    'focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-500',
                    'transition-colors',
                    error && !name.trim()
                      ? 'border-red-300 focus:border-red-500 focus:ring-red-500/20'
                      : 'border-slate-200',
                  )}
                />
              </div>
            </div>

            {/* Bot 简介 */}
            <div>
              <label
                htmlFor="summary"
                className="block text-sm font-medium text-slate-700 mb-1.5"
              >
                Bot 简介
              </label>
              <textarea
                id="summary"
                value={summary}
                onChange={(e) => setSummary(e.target.value)}
                placeholder="描述一下你的 Bot 能做什么..."
                rows={4}
                className={cn(
                  'w-full px-3 py-2.5 rounded-lg border text-sm resize-none',
                  'placeholder:text-slate-400',
                  'focus:outline-none focus:ring-2 focus:ring-lavender-500/20 focus:border-lavender-500',
                  'transition-colors',
                  'border-slate-200',
                )}
              />
              <p className="mt-1.5 text-xs text-slate-400">
                简介将展示给其他用户，帮助他们了解你的 Bot
              </p>
            </div>

            {/* 提交按钮 - 合并了授权功能 */}
            <Button
              type="submit"
              variant="primary"
              fullWidth
              loading={isLoading || isAuthorizing}
              disabled={!name.trim() || !token || isLoading || isAuthorizing}
            >
              {isLoading
                ? '提交中...'
                : isAuthorizing
                ? '授权中...'
                : '提交注册'}
            </Button>
          </form>
        </div>

        {/* 底部提示 */}
        <p className="mt-4 text-center text-xs text-slate-400">
          入网后，你的 Bot 将可以被其他用户发现和协作
        </p>
      </div>

      {/* 授权弹窗 - 与 CreateBotModal 保持一致 */}
      {showAuthIframe && authIframe && (
        <div className="fixed inset-0 z-[200]">
          <iframe
            src={authIframe}
            className="w-full h-full border-none"
            title="Bot 授权"
          />
        </div>
      )}
    </div>
  );
}
