import { Button } from '@/components/ui/Button';
import type { BotEditorSkill } from '@/domain/botEditor';
import { FolderUp } from 'lucide-react';
import { LocalSkillsPanel } from './LocalSkillsPanel';

interface CapabilityPickerLocalSkillsProps {
  visible: boolean;
  skills: BotEditorSkill[];
  editable: boolean;
  canPickDirectory: boolean;
  uploading: boolean;
  onUploadFolder?: () => Promise<void>;
  onLocalToggle?: (skill: BotEditorSkill) => Promise<void>;
  onLocalDelete?: (id: string) => Promise<void>;
  onLocalUpload?: (file: File) => Promise<void>;
}

export function CapabilityPickerLocalSkills(props: CapabilityPickerLocalSkillsProps) {
  if (!props.visible) return null;
  return (
    <>
      {props.onUploadFolder ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border p-3">
          <p className="m-0 min-w-0 flex-1 text-xs text-muted-foreground">
            仅显示当前 Bot 上传的本地 Skill（含已激活和未激活项），勾选并确认后加入当前能力集。
          </p>
          <Button
            variant="secondary"
            leftIcon={<FolderUp className="size-4" />}
            disabled={props.uploading || !props.canPickDirectory}
            onClick={() => void props.onUploadFolder?.()}
          >
            {props.uploading ? '上传中…' : props.canPickDirectory ? '上传本地目录' : '当前浏览器不支持'}
          </Button>
        </div>
      ) : null}
      {props.onLocalToggle && props.onLocalDelete && props.onLocalUpload ? (
        <LocalSkillsPanel
          embedded
          skills={props.skills}
          editable={props.editable}
          onToggle={props.onLocalToggle}
          onDelete={props.onLocalDelete}
          onUpload={props.onLocalUpload}
        />
      ) : null}
    </>
  );
}
