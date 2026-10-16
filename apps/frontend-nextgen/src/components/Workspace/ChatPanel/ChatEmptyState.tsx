// 消息区空态：真空会话的邀请开场。结构走「主视觉标记 + 主文案 + 副文案 + 行动」
// 的常规空态解剖，不借用消息几何（预演气泡/光珠会被误读为「已有消息在回复」，
// 第一轮预览被否，2026-10-12）——
// - 主视觉用 Bot 真身头像（48px + ring 软框），比通用 Inbox/Sparkles 图标更有指向性：
//   这是「与{Bot}的对话」，页面其余部分保持安静；
// - 主文案保留原句「发送一条消息开始对话」（指令式、无歧义）；
// - 副文案用 target.summary——真实内容优先于装饰语句，缺省不渲染；
// - 行动区为「开场菜单」：按命令面板装配物渲染 /file、/skill 的说明卡（图标 +
//   标题 + 一行真实说明 + 命令提示），点击一键写入输入框并聚焦——SenderRef.insert
//   走 commitValue→commandHandlers.onValueChange（Sender.js），命令面板随之自然唤起，
//   与「键入 / 查看全部指令」的既有心智（Sender placeholder 同款话术）打通。
import { Avatar, Button } from '@/components/ui';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import type { CommandConfig } from '@tc-chat/ui/es/Sender';
import { FileIcon, Zap } from 'lucide-react';
import { useMemo } from 'react';

interface ChatEmptyOpener {
  key: string;
  label: string;
  /** 一行说明：讲清这条指令实际做什么，只写已装配的真实能力。 */
  description: string;
  /** 命令真身，卡内以 code 提示——见样学样，教「/ 唤起全部指令」。 */
  hint: string;
  /** 写入输入框的触发文本（命令面板按 / 前缀 + query 匹配，无需尾随空格）。 */
  insert: string;
  icon: React.ReactNode;
}

interface ChatEmptyStateProps {
  target: ConversationTarget;
  /** 命令面板装配（useBotSessionFilesFeature 装配物），决定开场卡取舍；未提供则不渲染。 */
  command?: CommandConfig;
  /** 点击卡的落地动作：写入输入框并聚焦（宿主接 senderRef；不可交互时不传即隐藏行动区）。 */
  onInsertDraft?: (text: string) => void;
}

/** 按 Bot 会话命令项（botSessionCommandItems 的稳定 id）取舍开场卡。 */
function buildOpeners(command: CommandConfig | undefined): ChatEmptyOpener[] {
  const ids = new Set(command?.categories.flatMap((category) => category.items?.map((item) => item.id) ?? []) ?? []);
  return [
    ids.has('__file_entry__') && {
      key: 'file',
      label: '引用文件',
      description: '让 Bot 读取你在本会话上传的文件',
      hint: '/file',
      insert: '/file',
      icon: <FileIcon className="h-4 w-4" aria-hidden="true" />,
    },
    ids.has('__skill_entry__') && {
      key: 'skill',
      label: '调用技能',
      description: '挑一个已激活的技能直接开工',
      hint: '/skill',
      insert: '/skill',
      icon: <Zap className="h-4 w-4" aria-hidden="true" />,
    },
  ].filter(Boolean) as ChatEmptyOpener[];
}

export function ChatEmptyState({ target, command, onInsertDraft }: ChatEmptyStateProps) {
  const botName = target.name || '未命名 Bot';
  const openers = useMemo(() => buildOpeners(command), [command]);

  return (
    <div className="flex w-full max-w-md flex-col items-center gap-1 text-center">
      <div className="flex size-12 items-center justify-center rounded-full ring-8 ring-muted/60">
        <Avatar name={botName} src={target.avatar} size={48} />
      </div>
      <p className="m-0 mt-3 text-sm font-medium text-foreground">发送一条消息开始对话</p>
      {target.summary ? (
        <p className="m-0 max-w-sm text-[13px] leading-6 text-muted-foreground">{target.summary}</p>
      ) : null}
      {onInsertDraft && openers.length > 0 ? (
        <div className="mt-6 flex w-full max-w-sm flex-col gap-2">
          <p className="m-0 text-xs text-muted-foreground">试试这样开场</p>
          {openers.map((opener) => (
            <Button
              key={opener.key}
              variant="outline"
              onClick={() => onInsertDraft(opener.insert)}
              className="flex h-auto w-full items-center justify-start gap-3 rounded-xl border-border bg-background px-4 py-3 font-normal text-left hover:border-primary/40 hover:bg-selected"
            >
              <span
                className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"
                aria-hidden="true"
              >
                {opener.icon}
              </span>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="text-[13px] text-content-strong">{opener.label}</span>
                <span className="text-xs text-muted-foreground">{opener.description}</span>
              </span>
              <code className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                {opener.hint}
              </code>
            </Button>
          ))}
          <p className="m-0 mt-1 text-xs text-muted-foreground">也可以在输入框键入 / 查看全部指令</p>
        </div>
      ) : null}
    </div>
  );
}