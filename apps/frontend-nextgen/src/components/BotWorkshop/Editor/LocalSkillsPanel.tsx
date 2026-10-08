import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Input } from '@/components/ui/Input';
import type { BotEditorSkill } from '@/domain/botEditor';
import { useRef, useState } from 'react';
export function LocalSkillsPanel({
  skills,
  editable,
  onToggle,
  onDelete,
  onUpload,
}: {
  skills: BotEditorSkill[];
  editable: boolean;
  onToggle: (skill: BotEditorSkill) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onUpload: (file: File) => Promise<void>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="space-y-3 border-t border-border p-5">
      <h3 className="text-sm font-semibold">本地 Skill</h3>
      <p className="text-xs text-muted-foreground">上传同名 Skill 会替换已有内容。删除前请先停用并移除能力集引用。</p>
      <Input
        ref={input}
        type="file"
        accept=".zip,application/zip"
        className="hidden"
        aria-label="上传 Skill ZIP"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) void run(() => onUpload(file)).catch(() => undefined);
        }}
      />
      <Button variant="outline" disabled={!editable || busy} onClick={() => input.current?.click()}>
        上传 / 替换 ZIP
      </Button>
      {skills.map((skill) => (
        <div key={skill.id} className="flex items-center gap-2 rounded-md border border-border p-2">
          <span className="min-w-0 flex-1 truncate text-sm">{skill.name}</span>
          <Button
            size="sm"
            variant="outline"
            disabled={!editable || busy}
            onClick={() => void run(() => onToggle(skill)).catch(() => undefined)}
          >
            {skill.active ? '停用' : '启用'}
          </Button>
          <ConfirmDialog
            title="删除本地 Skill"
            description={`确认删除「${skill.name}」的本地内容？`}
            disabled={!editable || busy || skill.active}
            onConfirm={() => run(() => onDelete(skill.id))}
            confirmVariant="destructive"
          >
            <Button variant="ghost" size="sm">
              删除
            </Button>
          </ConfirmDialog>
        </div>
      ))}
    </section>
  );
}
