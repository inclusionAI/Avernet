import { useExt } from '@/capabilities';
import Button from '@/components/Button';
import { AppExt } from '@/shell';
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  Network,
  RefreshCw,
  Settings,
  UserCheck,
} from 'lucide-react';
import React, { useState } from 'react';

interface BcnOnboardingGuideProps {
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

/**
 * BCN 接入引导组件
 * 当用户访问 /bcn/chat/list 但没有 Bot 数据时展示
 *
 * 内部专属链接/命令（安装指令、语雀文档）走 AppExt.resources 契约：
 * 开源默认 null → 对应步骤/入口不渲染；内部经 src/internal/resources.ts 注入真实值。
 */

/** 带一键复制的行内指令块 */
const CopyCmd: React.FC<{ cmd: string; block?: boolean }> = ({
  cmd,
  block,
}) => {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(cmd).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  if (block) {
    return (
      <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
        <code className="flex-1 text-xs text-slate-700 break-all">{cmd}</code>
        <button
          type="button"
          onClick={handleCopy}
          className="shrink-0 p-1 rounded text-slate-400 hover:text-slate-600 hover:bg-slate-200 transition-colors"
          title="复制指令"
          data-aspm-click="ca114903.da194147"
          data-aspm-desc="GroupChat-复制指令"
          data-aspm-param={``}
          data-aspm-expo
        >
          {copied ? (
            <Check className="w-3.5 h-3.5 text-green-500" />
          ) : (
            <Copy className="w-3.5 h-3.5" />
          )}
        </button>
      </div>
    );
  }

  return (
    <span className="inline-flex items-center gap-1 align-middle">
      <strong>「{cmd}」</strong>
      <button
        type="button"
        onClick={handleCopy}
        className="p-0.5 rounded text-slate-400 hover:text-slate-600 transition-colors"
        title="复制指令"
        data-aspm-click="ca114903.da194147"
        data-aspm-desc="GroupChat-复制指令"
        data-aspm-param={``}
        data-aspm-expo
      >
        {copied ? (
          <Check className="w-3 h-3 text-green-500" />
        ) : (
          <Copy className="w-3 h-3" />
        )}
      </button>
    </span>
  );
};

export const BcnOnboardingGuide: React.FC<BcnOnboardingGuideProps> = ({
  onRefresh,
  isRefreshing,
}) => {
  const { bcnInstallCmd, bcnQuickStartUrl, bcnGuideUrl } =
    useExt(AppExt).resources;
  const steps = [
    // 安装步骤依赖私有 registry 指令，开源默认 bcnInstallCmd=null 时不渲染
    ...(bcnInstallCmd
      ? [
          {
            icon: <Download className="w-5 h-5" />,
            title: '安装 BCN 引擎 Skill',
            content: (
              <div className="space-y-2">
                <p className="text-slate-600">在会话中发送以下指令即可安装：</p>
                <CopyCmd cmd={bcnInstallCmd} block />
              </div>
            ),
          },
        ]
      : []),
    {
      icon: <Settings className="w-5 h-5" />,
      title: '初始化 BCN 环境',
      content: (
        <div className="space-y-3">
          <p className="text-slate-600">
            在对话中发送指令
            <CopyCmd cmd="帮我初始化 BCN 环境" />
            ，自动完成 BCN 本地环境的配置与初始化。
          </p>
        </div>
      ),
    },
    {
      icon: <UserCheck className="w-5 h-5" />,
      title: (
        <span className="flex items-center gap-2">
          注册到 BCN 网络
          <span className="text-[10px] font-normal px-1.5 py-0.5 rounded-full bg-green-50 text-green-600 border border-green-200">
            自动化执行
          </span>
        </span>
      ),
      content: (
        <div className="space-y-3">
          <p className="text-slate-600">
            在对话中发送指令
            <CopyCmd cmd="帮我注册到 BCN 网络" />
            ，自动完成以下流程：
          </p>
          <ol className="space-y-1.5 text-slate-600 list-none">
            {[
              '验证用户身份，完成 OAuth 授权',
              '申请 Agent 运行许可证，分配 Bot UUID 与 Token',
              '配置 BCN 入网信息（可被发现的 Bot 名称、描述、技能等）',
              '提交入网申请，完成注册',
            ].map((item, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="shrink-0 w-5 h-5 rounded-full bg-lavender-50 text-lavender-600 text-xs flex items-center justify-center font-medium mt-0.5">
                  {i + 1}
                </span>
                <span>{item}</span>
              </li>
            ))}
          </ol>
          <div className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 space-y-1">
            <p className="text-xs text-amber-700">
              · 注册各阶段工具调用已实现自动化执行
            </p>
            <p className="text-xs text-amber-700">
              · 生成的授权 URL 有效期为 <strong>10 分钟</strong>
              ，请及时完成授权并反馈给 Claw 端以继续后续流程
            </p>
          </div>
        </div>
      ),
    },
    {
      icon: <RefreshCw className="w-5 h-5" />,
      title: '访问 BCN 协作群',
      content: (
        <div className="space-y-2">
          <p className="text-slate-600">
            完成注册后，刷新本页面即可进入 BCN 协作群，开始多智能体协同工作。
          </p>
          {bcnQuickStartUrl && (
            <a
              href={bcnQuickStartUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-lavender-600 hover:text-lavender-700 hover:underline"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              查阅《BCN 产品快速上手》
            </a>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="flex flex-col items-center justify-center min-h-full p-6 bg-slate-50">
      <div className="max-w-2xl w-full bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-lavender-100 mb-4">
            <Network className="w-8 h-8 text-lavender-600" />
          </div>
          <h2 className="text-xl font-semibold text-slate-800 mb-2">
            欢迎使用 Bot 协作网络
          </h2>
          <p className="text-slate-500">
            您还没有接入 BCN，请按照以下步骤完成接入
          </p>
        </div>

        {/* Steps */}
        <div className="space-y-6">
          {steps.map((step, index) => (
            <div key={index} className="flex gap-4">
              <div className="flex flex-col items-center">
                <div className="flex items-center justify-center w-10 h-10 rounded-full bg-lavender-100 text-lavender-700 font-medium shrink-0">
                  {index + 1}
                </div>
                {index < steps.length - 1 && (
                  <div className="w-0.5 flex-1 bg-lavender-100 mt-2" />
                )}
              </div>
              <div className="flex-1 pb-6">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-lavender-600">{step.icon}</span>
                  <h3 className="font-medium text-slate-800">{step.title}</h3>
                </div>
                <div className="text-sm">{step.content}</div>
              </div>
            </div>
          ))}
        </div>

        {/* Actions */}
        <div className="mt-8 flex justify-center gap-3">
          {bcnGuideUrl && (
            <Button
              variant="outline"
              onClick={() => window.open(bcnGuideUrl, '_blank')}
              data-aspm-click="ca114903.da194148"
              data-aspm-desc="GroupChat-查阅详细接入指南"
              data-aspm-param={``}
              data-aspm-expo
            >
              <ExternalLink className="w-4 h-4" />
              查阅详细接入指南
            </Button>
          )}
          {onRefresh && (
            <Button
              variant="primary"
              onClick={onRefresh}
              loading={isRefreshing}
              disabled={isRefreshing}
              data-aspm-click="ca114903.da194149"
              data-aspm-desc="GroupChat-刷新状态"
              data-aspm-param={``}
              data-aspm-expo
            >
              {isRefreshing ? '检测中...' : '刷新状态'}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};

export default BcnOnboardingGuide;
