/**
 * FuseChatPanel - 智能问答悬浮聊天窗口
 *
 * 跟随按钮位置的悬浮窗口，非模态
 * 支持选择画像融合 Bot 成员后提交问题
 */

import BotAvatar from '@/components/BotAvatar';
import Button from '@/components/Button';
import Empty from '@/components/Empty';
import { getExt } from '@/capabilities';
import type { GroupInfo } from '@/pages/GroupChat/types';
import { AppExt } from '@/shell/extension';
import { cn } from '@/utils/utils';
import type { Block } from '@aix-chat/core';
import { aixUiPlugin, Bubble } from '@aix-chat/ui';
import { Brain, Check, Copy, Loader2, Send, X } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useFuse } from '@/pages/GroupChat/hooks/useFuse';

const PANEL_WIDTH = 380;
const PANEL_HEIGHT = 640;

interface FuseChatPanelProps {
  group: GroupInfo | null;
  /** 当前活跃会话 ID，用于按 session 隔离 fuse 对话 */
  sessionId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const FuseChatPanel: React.FC<FuseChatPanelProps> = ({
  group,
  sessionId,
  open,
  onOpenChange,
}) => {
  const {
    messages,
    isFusing,
    submitQuestion,
    clearSessionMessages,
    fusionBots,
    isLoadingFusionBots,
  } = useFuse(open ? group : null, open ? sessionId : null);

  const [inputValue, setInputValue] = useState('');
  const [selectedBotIds, setSelectedBotIds] = useState<Set<string>>(new Set());
  const [panelWidth, setPanelWidth] = useState(PANEL_WIDTH);
  const [isDragging, setIsDragging] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(PANEL_WIDTH);

  // open 默认只启用公开 AixUI 插件；internal overlay 如存在则追加 aixPanelPlugin。
  const markdownExtensions = useMemo(() => {
    const ext = getExt(AppExt) as unknown as {
      chatExtensions?: {
        getMarkdownExtensions?: () => (typeof aixUiPlugin)[];
      };
    };
    return [
      aixUiPlugin,
      ...(ext.chatExtensions?.getMarkdownExtensions?.() ?? []),
    ];
  }, []);

  // 拖动调整宽度（左侧边缘：向左拖变宽，向右拖变窄，右侧固定）
  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(true);
      dragStartX.current = e.clientX;
      dragStartWidth.current = panelWidth;
      document.body.style.cursor = 'ew-resize';
      document.body.style.userSelect = 'none';
    },
    [panelWidth],
  );

  useEffect(() => {
    if (!isDragging) return;

    const handleMouseMove = (e: MouseEvent) => {
      const delta = e.clientX - dragStartX.current;
      // 左侧边缘拖动：向右拖变窄，向左拖变宽（右侧固定）
      const newWidth = Math.min(
        Math.max(dragStartWidth.current - delta, 280),
        600,
      );
      setPanelWidth(newWidth);
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isDragging]);

  // 点击外部关闭
  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onOpenChange(false);
      }
    };
    // 延迟绑定，避免打开时的点击立即关闭
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handleClickOutside);
    }, 0);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [open, onOpenChange]);

  // 计算面板位置：在按钮左边，挨着按钮
  const panelPos = useMemo(() => {
    // 按钮位置：right: 24px, bottom: 80px, width: 40px, height: 40px
    // 面板放在按钮左边，间距 8px
    // x = window.innerWidth - 24 - 40 - 8 - panelWidth = window.innerWidth - panelWidth - 72
    let x = window.innerWidth - panelWidth - 72;
    // y = 按钮顶部位置 = window.innerHeight - 80 - 40 = window.innerHeight - 120
    // 但需要确保面板底部不超出视口
    let y = window.innerHeight - 120 - PANEL_HEIGHT;

    // 确保不超出顶部
    if (y < 8) {
      y = 8;
    }
    // 确保不超出左侧
    if (x < 8) {
      x = 8;
    }

    return { x, y };
  }, [panelWidth]);

  // 可选的融合 Bot（已开启画像公开）
  const availableBots = useMemo(
    () => fusionBots.filter((b) => b.fusionEnable),
    [fusionBots],
  );

  const isAllSelected =
    availableBots.length > 0 &&
    availableBots.every((b) => selectedBotIds.has(b.botUuid));

  const toggleBot = useCallback((botUuid: string) => {
    setSelectedBotIds((prev) => {
      const next = new Set(prev);
      if (next.has(botUuid)) {
        next.delete(botUuid);
      } else {
        next.add(botUuid);
      }
      return next;
    });
  }, []);

  const toggleAll = useCallback(() => {
    if (isAllSelected) {
      setSelectedBotIds(new Set());
    } else {
      setSelectedBotIds(new Set(availableBots.map((b) => b.botUuid)));
    }
  }, [isAllSelected, availableBots]);

  const handleSubmit = useCallback(() => {
    if (!inputValue.trim() || isFusing) return;
    let ids: string[] | undefined;
    if (selectedBotIds.size > 0) {
      ids = Array.from(selectedBotIds);
    } else {
      // 未手动选择时，默认使用所有 fusionEnable 的 Bot，并同步到 selectedBotIds
      const defaultIds = availableBots
        .filter((b) => b.fusionEnable)
        .map((b) => b.botUuid);
      ids = defaultIds;
      setSelectedBotIds(new Set(defaultIds));
    }
    submitQuestion(inputValue, ids);
    setInputValue('');
    setTimeout(() => {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: 'smooth',
      });
    }, 100);
  }, [inputValue, isFusing, selectedBotIds, availableBots, submitQuestion]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  // 复制消息
  const handleCopy = useCallback((msgId: string, content: string) => {
    navigator.clipboard.writeText(content);
    setCopiedId(msgId);
    setTimeout(() => setCopiedId(null), 2000);
  }, []);

  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onOpenChange(false);
    };
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [open, onOpenChange]);

  // 将文本转换为 blocks 格式
  const textToBlocks = (text: string): Block[] => [
    { type: 'text', content: text },
  ];

  // 渲染消息气泡
  const renderMessage = (msg: (typeof messages)[0]) => {
    const blocks = textToBlocks(msg.content);

    if (msg.role === 'user') {
      return (
        <div key={msg.id} className="flex justify-end w-full">
          <div className="w-[100%]">
            {msg.participants && msg.participants.length > 0 && (
              <div className="flex flex-wrap justify-start gap-1 mb-1.5">
                {msg.participants.map((p) => (
                  <div
                    key={p.id}
                    className="flex items-center gap-1 px-2 py-0.5 bg-lavender-100 rounded-full"
                  >
                    <BotAvatar
                      type="assistant"
                      size="xs"
                      name={p.name}
                      avatarUrl={p.avatar}
                      botId={p.id?.split(':')[0]}
                    />
                    <span className="text-[10px] text-lavender-700 max-w-[60px] truncate">
                      {p.name}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <Bubble
              sender={{ role: 'user', maxWidth: '90%' }}
              blocks={blocks}
              markdown={{
                preset: 'full',
                extensions: markdownExtensions,
              }}
            />
          </div>
        </div>
      );
    }

    return (
      <div key={msg.id} className="gap-2 w-full group">
        <div className="flex gap-2 w-full group">
          <BotAvatar type="assistant" size="sm" name="智能问答" />
          <div className="w-[100%]">
            {msg.isLoading ? (
              <div className="flex items-center gap-2 text-slate-400 px-3 py-2 bg-slate-100 rounded-2xl rounded-bl-sm text-sm">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>正在融合多个 Bot 的智慧...</span>
              </div>
            ) : (
              <Bubble
                role="assistant"
                blocks={blocks}
                showAvatar={false}
                markdown={{
                  preset: 'full',
                  extensions: markdownExtensions,
                }}
                sender={{ maxWidth: '90%' }}
              />
            )}
          </div>
        </div>
        {/* 复制按钮放在消息后面 */}
        {!msg.isLoading && (
          <div className="flex justify-end w-full">
            <button
              type="button"
              onClick={() => handleCopy(msg.id, msg.content)}
              className="flex-shrink-0 p-1.5 rounded-md hover:bg-slate-100 opacity-0 group-hover:opacity-100 transition-opacity self-start mt-1"
              title="复制"
              data-aspm-click="ca114903.da194189"
              data-aspm-desc="GroupChat-复制融合消息"
              data-aspm-param={``}
              data-aspm-expo
            >
              {copiedId === msg.id ? (
                <Check className="w-3.5 h-3.5 text-green-500" />
              ) : (
                <Copy className="w-3.5 h-3.5 text-slate-400" />
              )}
            </button>
          </div>
        )}
      </div>
    );
  };

  const hasMessages = messages.length > 0;

  // 清除当前 session 的消息
  const handleClearMessages = useCallback(() => {
    if (sessionId) {
      clearSessionMessages(sessionId);
    }
    setSelectedBotIds(new Set());
  }, [sessionId, clearSessionMessages]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          ref={panelRef}
          key="fuse-panel"
          initial={{ opacity: 0, scale: 0.92, x: panelPos.x, y: panelPos.y }}
          animate={{ opacity: 1, scale: 1, x: panelPos.x, y: panelPos.y }}
          exit={{ opacity: 0, scale: 0.92 }}
          transition={{ duration: 0.15, ease: 'easeOut' }}
          className="fixed z-40 flex flex-col bg-white rounded-2xl shadow-2xl border border-slate-200/80 overflow-hidden"
          style={{
            left: 0,
            top: 0,
            width: panelWidth,
            height: PANEL_HEIGHT,
            transformOrigin: 'bottom right',
          }}
        >
          {/* 拖动调整宽度把手 - 左侧 */}
          <div
            className="absolute left-0 top-0 bottom-0 w-1.5 cursor-ew-resize hover:bg-lavender-300 transition-colors z-10"
            onMouseDown={handleMouseDown}
          />
          {/* 标题栏 */}
          <div className="flex flex-col px-4 py-3 border-b border-slate-100 flex-shrink-0">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Brain className="w-4 h-4 text-lavender-500" />
                <span className="text-sm font-medium text-slate-800">
                  融合模式
                </span>
              </div>
              <button
                type="button"
                onClick={() => onOpenChange(false)}
                className="p-1 rounded-md hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-colors"
                data-aspm-click="ca114903.da194190"
                data-aspm-desc="GroupChat-关闭融合模式"
                data-aspm-param={``}
                data-aspm-expo
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <span className="text-xs text-slate-400 mt-1">
              用户可基于当前协作群成员画像与会话上下文进行深度问答
            </span>
          </div>

          {/* Bot 选择区域 / 取消选择 */}
          {hasMessages ? (
            <div className="px-4 py-3 border-b border-slate-100 flex-shrink-0">
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-500">
                  已选择 {selectedBotIds.size} 个融合成员
                </span>
                <button
                  type="button"
                  onClick={handleClearMessages}
                  className="text-xs text-slate-500 hover:text-slate-700"
                  data-aspm-click="ca114903.da194191"
                  data-aspm-desc="GroupChat-取消选择融合成员"
                  data-aspm-param={``}
                  data-aspm-expo
                >
                  取消选择
                </button>
              </div>
            </div>
          ) : (
            <div className="px-4 pt-3 pb-2 border-b border-slate-100 flex-shrink-0">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-slate-700">
                  选择画像融合成员
                </span>
                {availableBots.length > 0 && (
                  <button
                    type="button"
                    onClick={toggleAll}
                    className="text-[10px] text-lavender-600 hover:text-lavender-700"
                    data-aspm-click="ca114903.da194192"
                    data-aspm-desc="GroupChat-全选融合成员"
                    data-aspm-param={``}
                    data-aspm-expo
                  >
                    {isAllSelected ? '取消全选' : '全选'}
                  </button>
                )}
              </div>
              {isLoadingFusionBots ? (
                <div className="flex items-center justify-center py-4">
                  <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                  <span className="ml-2 text-xs text-slate-400">加载中...</span>
                </div>
              ) : availableBots.length === 0 ? (
                <p className="text-xs text-slate-400 py-2">
                  协作群内无Bot公开画像，融合模式暂不可用
                </p>
              ) : (
                <>
                  <div className="flex flex-wrap gap-3">
                    {availableBots.map((bot) => {
                      const isSelected = selectedBotIds.has(bot.botUuid);
                      return (
                        <button
                          key={bot.botUuid}
                          type="button"
                          onClick={() => toggleBot(bot.botUuid)}
                          className="flex flex-col items-center gap-1 group"
                          data-aspm-click="ca114903.da194193"
                          data-aspm-desc="GroupChat-选择融合成员"
                          data-aspm-param={``}
                          data-aspm-expo
                        >
                          <div className="relative">
                            <BotAvatar
                              type="assistant"
                              size="md"
                              name={bot.name}
                              avatarUrl={bot.avatar}
                              botId={bot.botUuid?.split(':')[0]}
                            />
                            <div
                              className={cn(
                                'absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full flex items-center justify-center transition-colors',
                                isSelected
                                  ? 'bg-lavender-600'
                                  : 'bg-slate-200 group-hover:bg-slate-300',
                              )}
                            >
                              {isSelected && (
                                <Check className="w-2.5 h-2.5 text-white" />
                              )}
                            </div>
                          </div>
                          <span
                            className={cn(
                              'text-[10px] max-w-[56px] truncate',
                              isSelected
                                ? 'text-lavender-600 font-medium'
                                : 'text-slate-500',
                            )}
                          >
                            {bot.name}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  {selectedBotIds.size > 0 && (
                    <p className="text-[10px] text-slate-400 mt-2">
                      已选 {selectedBotIds.size}/{availableBots.length} 个Bot
                    </p>
                  )}
                </>
              )}
            </div>
          )}

          {/* 消息列表 */}
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto px-4 py-3 space-y-3"
          >
            {messages.length === 0 ? (
              <Empty size="md" title="暂无消息" className="h-full" />
            ) : (
              messages.map(renderMessage)
            )}
          </div>

          {/* 输入区域 */}
          <div className="border-t border-slate-100 p-3 flex-shrink-0">
            <div className="flex gap-2">
              <div className="flex-1 relative">
                <input
                  className={cn(
                    'w-full px-3 py-2 text-sm rounded-lg border border-slate-200',
                    'focus:outline-none focus:ring-2 focus:ring-lavender-400 focus:border-lavender-400',
                    'placeholder:text-slate-400',
                    'disabled:opacity-50 disabled:cursor-not-allowed',
                  )}
                  placeholder="输入问题..."
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={isFusing}
                />
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={handleSubmit}
                loading={isFusing}
                disabled={!inputValue.trim() || isFusing}
                className="px-3"
                data-aspm-click="ca114903.da194194"
                data-aspm-desc="GroupChat-提交融合问题"
                data-aspm-param={``}
                data-aspm-expo
              >
                <Send className="w-4 h-4" />
              </Button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default FuseChatPanel;
