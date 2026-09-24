import { Button, IconButton, Input } from '@/components/ui';
import { Pencil } from 'lucide-react';
import { useState } from 'react';

export interface EditableNameProps {
  /** 查看态展示的当前值 */
  value: string;
  /** 是否可编辑（无可管理权限时不显示入口） */
  canEdit: boolean;
  /** 入口与输入框的可访问名称（如「编辑群名称」） */
  editLabel: string;
  onSave: (next: string) => void;
}

/**
 * 低频名称修改的查看优先组件（方案 A+ D5）：
 * 查看态纯文本 + 常驻铅笔入口（入口可见，不悬停显隐）；
 * 点击进入编辑态（输入框 + 保存/取消），Enter 保存 / Esc 取消；
 * 空值或未变化时保存等同取消，不触发回调。
 */
export function EditableName({ value, canEdit, editLabel, onSave }: EditableNameProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  const start = () => {
    setDraft(value);
    setEditing(true);
  };

  const cancel = () => setEditing(false);

  const save = () => {
    const next = draft.trim();
    setEditing(false);
    if (next && next !== value) onSave(next);
  };

  if (!editing) {
    return (
      <div className="flex items-center gap-1">
        <p className="m-0 min-w-0 flex-1 truncate text-sm font-medium text-foreground">{value}</p>
        {canEdit ? (
          // 入口视觉降噪：常态灰色铅笔，悬停仅变色不出底色块（避免标题行右上角出现圆角矩形块）。
          <IconButton
            label={editLabel}
            icon={<Pencil className="h-3.5 w-3.5" aria-hidden />}
            size="sm"
            className="h-6 w-6 rounded-md text-muted-foreground hover:bg-transparent hover:text-foreground"
            onClick={start}
          />
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex gap-2">
      <Input
        value={draft}
        autoFocus
        aria-label={editLabel}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') save();
          else if (event.key === 'Escape') cancel();
        }}
      />
      <Button size="sm" onClick={save}>
        保存
      </Button>
      <Button variant="ghost" size="sm" onClick={cancel}>
        取消
      </Button>
    </div>
  );
}
