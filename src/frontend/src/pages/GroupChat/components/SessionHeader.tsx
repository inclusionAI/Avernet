/**
 * SessionHeader - 会话详情头部
 *
 * 显示可编辑的会话标题、群组元信息
 * 标题编辑：双击进入编辑态，Enter 保存，Esc 取消，最多 32 字
 * 中文输入法：组合输入期间不截断，结束后超长部分截断
 */

import { cn } from '@/utils/utils';
import { ArrowLeft, Pencil } from 'lucide-react';
import React, { useCallback, useEffect, useRef, useState } from 'react';

interface SessionHeaderProps {
  /** 会话 ID */
  sessionId?: string;
  /** 会话标题 */
  sessionTitle: string;
  /** 协作群名称 */
  groupName: string;
  /** 协作目标 */
  groupGoal?: string;
  /** 群成员数量 */
  groupMemberCount: number;
  /** 会话成员数量 */
  sessionMemberCount: number;
  /** 标题更新回调 */
  onTitleUpdate: (title: string) => void;
  /** 返回回调 */
  onBack: () => void;
  /** 是否正在更新标题 */
  isUpdatingTitle?: boolean;
}

const MAX_TITLE_LENGTH = 32;

const SessionHeader: React.FC<SessionHeaderProps> = ({
  sessionTitle,
  groupName,
  groupGoal,
  groupMemberCount,
  sessionMemberCount,
  onTitleUpdate,
  onBack,
  isUpdatingTitle,
}) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(sessionTitle);
  const [isComposing, setIsComposing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // 进入编辑态时聚焦
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  // 同步外部标题变化
  useEffect(() => {
    if (!isEditing) {
      setEditValue(sessionTitle);
    }
  }, [sessionTitle, isEditing]);

  const handleStartEdit = useCallback(() => {
    if (isUpdatingTitle) return;
    setEditValue(sessionTitle);
    setIsEditing(true);
  }, [sessionTitle, isUpdatingTitle]);

  const handleSave = useCallback(() => {
    const trimmed = editValue.trim().slice(0, MAX_TITLE_LENGTH);
    if (trimmed && trimmed !== sessionTitle) {
      onTitleUpdate(trimmed);
    }
    setIsEditing(false);
  }, [editValue, sessionTitle, onTitleUpdate]);

  const handleCancel = useCallback(() => {
    setEditValue(sessionTitle);
    setIsEditing(false);
  }, [sessionTitle]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const value = e.target.value;
      // 组合输入中（如中文拼音）允许超长，等组合结束后再截断
      if (isComposing) {
        setEditValue(value);
        return;
      }
      if (value.length <= MAX_TITLE_LENGTH) {
        setEditValue(value);
      } else {
        setEditValue(value.slice(0, MAX_TITLE_LENGTH));
      }
    },
    [isComposing],
  );

  const handleCompositionEnd = useCallback(
    (e: React.CompositionEvent<HTMLInputElement>) => {
      const value = (e.target as HTMLInputElement).value;
      if (value.length > MAX_TITLE_LENGTH) {
        setEditValue(value.slice(0, MAX_TITLE_LENGTH));
      }
      setIsComposing(false);
    },
    [],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      // 组合输入中不拦截 Enter（用于选词）
      if (isComposing) return;
      if (e.key === 'Enter') {
        e.preventDefault();
        handleSave();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        handleCancel();
      }
    },
    [handleSave, handleCancel, isComposing],
  );

  return (
    <div className="px-4 py-3 border-b border-slate-100 bg-white">
      {/* 第一行：返回按钮 + 标题 + 操作按钮 */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onBack}
          className="p-1 rounded-md hover:bg-slate-100 transition-colors flex-shrink-0"
          title="返回会话列表"
        >
          <ArrowLeft className="w-4 h-4 text-slate-500" />
        </button>

        {isEditing ? (
          <div className="flex-1 min-w-0 flex items-center gap-1.5">
            <input
              ref={inputRef}
              type="text"
              value={editValue}
              onChange={handleChange}
              onCompositionStart={() => setIsComposing(true)}
              onCompositionEnd={handleCompositionEnd}
              onKeyDown={handleKeyDown}
              onBlur={handleSave}
              className="flex-1 min-w-0 text-sm font-medium text-slate-800 bg-slate-50 px-2 py-0.5 rounded border border-lavender-300 focus:outline-none focus:ring-1 focus:ring-lavender-400"
            />
            <span
              className={cn(
                'text-[10px] tabular-nums flex-shrink-0 whitespace-nowrap',
                editValue.length > MAX_TITLE_LENGTH
                  ? 'text-red-500'
                  : 'text-slate-400',
              )}
            >
              {Math.min(editValue.length, MAX_TITLE_LENGTH)}/{MAX_TITLE_LENGTH}
            </span>
          </div>
        ) : (
          <div className="flex items-center gap-1.5 flex-1 min-w-0">
            <span
              className="text-sm font-medium text-slate-800 truncate cursor-pointer hover:text-lavender-600 transition-colors"
              onClick={handleStartEdit}
              title="点击编辑标题"
            >
              {sessionTitle || '新会话'}
            </span>
            <button
              type="button"
              onClick={handleStartEdit}
              className="p-0.5 rounded hover:bg-slate-100 transition-colors flex-shrink-0"
              title="编辑标题"
            >
              <Pencil className="w-3 h-3 text-slate-400" />
            </button>
          </div>
        )}
      </div>

      {/* 第二行：群组元信息 */}
      <div className="mt-1 ml-6 text-xs text-slate-400 truncate">
        协作群：{groupName}
        {groupGoal && <span> · 协作目标：{groupGoal}</span>}
        <span> · 群成员数量：{groupMemberCount}个成员</span>
        <span> · 会话成员数量：{sessionMemberCount}个成员</span>
      </div>
    </div>
  );
};

export default SessionHeader;
