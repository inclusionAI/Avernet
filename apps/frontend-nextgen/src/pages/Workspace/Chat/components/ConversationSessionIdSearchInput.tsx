// 会话 ID 检索输入行(Bot 展开区顶部,🔍 开关显示/隐藏)。
// 契约对齐搜索提交语义:value = 已生效 sessionKey 条件(空串 = 未生效),onSearch = 提交(组件内 trim,空串清除);
// 输入为本地草稿,Enter 提交生效;清空按钮提交空串恢复全列表。
// 实例范围扩展(多实例服务 Bot,能力缺省时零变化):
// - disabled:实例范围未就绪(加载中/失败)时禁用输入;
// - preserveDraftOnExternalClear:外部清除已提交条件时保留输入草稿(如切换实例),默认 false = 值变化同步草稿。
import { Button, Input } from '@/components/ui';
import { Search, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

export interface ConversationSessionIdSearchInputProps {
  /** 当前已生效的 sessionKey 条件;空串表示未生效。 */
  value: string;
  /** 提交草稿(组件内已 trim;空串清除条件)。 */
  onSearch(value: string): void;
  /** 实例范围未就绪(加载中/失败)时禁用输入;默认 false。 */
  disabled?: boolean;
  /** 外部清除已提交条件时保留输入草稿(如切换实例);默认 false=值变化同步草稿。 */
  preserveDraftOnExternalClear?: boolean;
}

/** 输入框:内嵌左前缀 🔍 图标 + 条件生效时的清空按钮;输入为草稿,Enter 提交,生效条件变化时草稿同步。 */
export function ConversationSessionIdSearchInput({
  value,
  onSearch,
  disabled = false,
  preserveDraftOnExternalClear = false,
}: ConversationSessionIdSearchInputProps) {
  const [draft, setDraft] = useState(value);
  // 内部清除(清空按钮)标记:外部值同步时区分「用户点清除」与「外部清除」两种值变空场景。
  const internalClearRef = useRef(false);
  // 已生效条件变化(提交生效/切范围清除)时,草稿同步回当前条件;
  // 外部清除且要求保草稿时(切换实例)不回写,输入草稿保留。
  useEffect(() => {
    const fromInternalClear = internalClearRef.current;
    internalClearRef.current = false;
    if (fromInternalClear || !preserveDraftOnExternalClear || value !== '') setDraft(value);
  }, [value, preserveDraftOnExternalClear]);
  const active = value !== '';
  const handleClear = () => {
    internalClearRef.current = true;
    onSearch('');
  };
  return (
    // conversation-area-in(2026-10-22 体验精修二轮):🔍 开关展开时的 2px 下落淡入,
    // 与 Bot 展开区同一动效语言(global.css 局部 keyframe,reduced-motion 静止)。
    <div className="conversation-area-in px-4 py-1.5">
      <div className="relative">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          value={draft}
          disabled={disabled}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            onSearch(draft.trim());
          }}
          placeholder="输入sessionID回车检索会话"
          aria-label="会话ID检索"
          className="h-7 w-full rounded-md pl-8 pr-6 text-xs"
        />
        {active && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="清空会话ID检索"
            onClick={handleClear}
            className="absolute right-1.5 top-1/2 h-4 w-4 -translate-y-1/2 rounded-sm text-muted-foreground hover:text-foreground"
          >
            <X className="h-3 w-3" aria-hidden="true" />
          </Button>
        )}
      </div>
    </div>
  );
}